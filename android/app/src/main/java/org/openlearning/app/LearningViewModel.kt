package org.openlearning.app

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import java.util.UUID

class LearningViewModel(application: Application) : AndroidViewModel(application) {
    private val container = (application as LearningApplication).container
    val connection = container.credentials.connection
    private var repo: LearningRepository? = null
    private var feedFlow: Job? = null
    private var jobsFlow: Job? = null
    private val mutable = MutableStateFlow(ScreenState())
    val state = mutable.asStateFlow()
    private var cached: List<CachedInsight> = emptyList()
    private var activeOperations = 0
    private var requestGeneration = 0
    private var chatGeneration = 0
    private var detailJob: Job? = null
    init {
        viewModelScope.launch {
            connection.collect { config ->
                feedFlow?.cancel(); jobsFlow?.cancel(); detailJob?.cancel()
                requestGeneration++; chatGeneration++; activeOperations = 0; cached = emptyList()
                mutable.value = ScreenState()
                repo = config?.let(container::repository)
                repo?.let { repository ->
                    feedFlow = launch { repository.cached.collect { rows ->
                        cached = rows
                        mutable.update { it.copy(feed = rows.filter { row -> row.inFeed }.map(repository::decode), library = rows.filter { row -> row.saved }.map(repository::decode)) }
                    } }
                    jobsFlow = launch {
                        var recover = true
                        repository.jobs.collect { jobs ->
                            mutable.update { it.copy(jobs = jobs) }
                            if (recover) {
                                recover = false
                                jobs.filter { it.status.lowercase() !in setOf("completed", "complete", "succeeded", "success", "done", "failed", "error", "cancelled", "paused") }
                                    .forEach { IngestWorker.schedule(getApplication(), it.scope, it.id) }
                            }
                        }
                    }
                    refresh()
                }
            }
        }
    }
    fun configure(server: String, token: String, http: Boolean): String? = try {
        container.credentials.save(server, token, http); null
    } catch (error: Exception) { error.message ?: "Could not save the connection." }
    private fun request(block: suspend (LearningRepository) -> Unit) {
        val repository = repo ?: return
        val generation = requestGeneration
        viewModelScope.launch {
            activeOperations++
            mutable.update { it.copy(busy = true, error = null) }
            try { block(repository) }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (generation == requestGeneration) mutable.update { it.copy(error = error.userMessage()) } }
            finally {
                if (generation == requestGeneration) {
                    activeOperations--
                    mutable.update { it.copy(busy = activeOperations > 0) }
                }
            }
        }
    }
    fun refresh() = request { repository ->
        supervisorScope {
            val results = listOf(async { repository.refreshFeed() }, async { repository.refreshLibrary() })
            var failure: Throwable? = null
            for (result in results) try { result.await() } catch (error: Exception) { if (error is CancellationException) throw error; failure = error }
            failure?.let { throw it }
        }
    }
    fun importUrl(input: String) {
        val url = sharedUrl(input.trim())
        if (url == null) { mutable.update { it.copy(error = "Enter an article URL beginning with https:// or http://.") }; return }
        request { repository ->
            val row = PendingIngest(repository.config.scope, UUID.randomUUID().toString(), url)
            container.database.dao().putJob(row)
            IngestWorker.schedule(getApplication(), row.scope, row.id)
        }
    }
    fun retryImport(job: PendingIngest) {
        request { repository ->
            if (job.scope != repository.config.scope) return@request
            val retry = if (job.jobId != null && job.status.lowercase() in listOf("failed", "error")) repository.api.retryJob(job.jobId) else null
            container.database.dao().putJob(job.copy(jobId = retry?.id ?: job.jobId, status = retry?.status ?: "queued", error = null))
            IngestWorker.schedule(getApplication(), job.scope, job.id, replace = true)
        }
    }
    fun save(item: Insight) = request { repository ->
        repository.save(item)
        if (repo === repository && state.value.selected?.id == item.id) mutable.update { it.copy(selected = it.selected?.copy(saved = true)) }
    }
    fun dismiss(item: Insight) = request { it.dismiss(item) }
    fun select(item: Insight) {
        detailJob?.cancel()
        chatGeneration++
        mutable.update { it.copy(selected = item, chat = emptyList(), chatBusy = false, revealed = false, learning = false) }
        val repository = repo ?: return
        detailJob = viewModelScope.launch {
            try {
                val fresh = repository.api.insight(item.id)
                repository.remember(fresh)
                if (repo === repository && state.value.selected?.id == item.id) mutable.update { it.copy(selected = fresh) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { /* Cached detail remains readable offline. */ }
        }
    }
    fun closeDetail() { detailJob?.cancel(); chatGeneration++; mutable.update { it.copy(selected = null, chat = emptyList(), chatBusy = false, learning = false) } }
    fun learn(item: Insight) { select(item); mutable.update { it.copy(learning = true) }; chat("") }
    fun chat(message: String) {
        val selected = state.value.selected ?: return
        if (state.value.chatBusy) return
        val repository = repo ?: return
        val generation = chatGeneration
        mutable.update { it.copy(chatBusy = true, error = null, chat = if (message.isBlank()) it.chat else it.chat + ChatLine("You", message)) }
        viewModelScope.launch {
            try {
                val answer = repository.api.chat(ChatRequest(selected.id, message))
                if (repo === repository && generation == chatGeneration) mutable.update { it.copy(chat = it.chat + ChatLine("Tutor", answer.answer, answer.question, answer.citations)) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (repo === repository && generation == chatGeneration) mutable.update { it.copy(error = error.userMessage()) } }
            finally { if (repo === repository && generation == chatGeneration) mutable.update { it.copy(chatBusy = false) } }
        }
    }
    fun loadReviews() = request { repository ->
        val reviews = repository.api.reviews()
        if (repo === repository) mutable.update { it.copy(reviews = reviews, reviewsLoaded = true, revealed = false, reviewSource = null) }
    }
    fun revealReview(review: Review) {
        request { repository ->
            val cached = container.database.dao().get(repository.config.scope, review.insightId)?.let(repository::decode)
            val item = cached ?: repository.api.insight(review.insightId)
            if (repo === repository && state.value.reviews.firstOrNull()?.id == review.id) mutable.update { it.copy(revealed = true, reviewSource = item) }
        }
    }
    fun rate(review: Review, rating: String) = request { repository ->
        val next = repository.api.rate(review.id, Rating(rating))
        if (repo === repository) mutable.update { it.copy(reviews = it.reviews.filterNot { r -> r.id == review.id }, revealed = false, reviewSource = null, notice = "Next review: ${next.dueAt}") }
    }
    fun search(query: String) = request { repository ->
        val results = repository.api.search(query.trim())
        if (repo === repository) mutable.update { it.copy(results = results, searched = true) }
    }
    fun clearError() { mutable.update { it.copy(error = null, notice = null) } }
}
data class ChatLine(val role: String, val text: String, val question: String = "", val citations: List<Citation> = emptyList())
data class ScreenState(
    val feed: List<Insight> = emptyList(), val library: List<Insight> = emptyList(),
    val results: List<Insight> = emptyList(), val searched: Boolean = false,
    val jobs: List<PendingIngest> = emptyList(), val reviews: List<Review> = emptyList(), val reviewsLoaded: Boolean = false,
    val selected: Insight? = null, val learning: Boolean = false, val chat: List<ChatLine> = emptyList(), val chatBusy: Boolean = false,
    val revealed: Boolean = false, val reviewSource: Insight? = null, val busy: Boolean = false, val error: String? = null, val notice: String? = null,
)
