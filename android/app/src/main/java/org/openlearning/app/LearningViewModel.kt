package org.openlearning.app

import android.app.Application
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import retrofit2.HttpException
import java.io.IOException

@OptIn(FlowPreview::class)
class LearningViewModel(application: Application, savedState: SavedStateHandle) : AndroidViewModel(application) {
    private val memory = TutorMemory(savedState)
    private var restorePending = true
    private val container = (application as LearningApplication).container
    val connection = container.credentials.connection
    private var repo: LearningRepository? = null
    private var purgeJob: Job? = null
    private var feedFlow: Job? = null
    private var jobsFlow: Job? = null
    private val mutable = MutableStateFlow(ScreenState())
    val state = mutable.asStateFlow()
    private var cached: List<CachedInsight> = emptyList()
    private var activeOperations = 0
    private var requestGeneration = 0
    private var chatGeneration = 0
    private var detailJob: Job? = null
    // The draft lives in Compose state and is persisted (debounced) to Room, never to saved instance state.
    var draft by mutableStateOf(NoteDraftFields()); private set
    var noteMessage by mutableStateOf<String?>(null); private set
    // A picked file waiting for the person to confirm replacing the text they typed. Held in memory only.
    var pendingFile by mutableStateOf<NoteFileResult.Loaded?>(null); private set
    private var draftReady = false
    private val draftLock = Mutex()
    init {
        viewModelScope.launch { snapshotFlow { draft }.debounce(400).collect { saveDraft(it) } }
        viewModelScope.launch {
            connection.collect { config ->
                val step = connectionStep(restorePending, config != null)
                val restore = step.restore
                restorePending = step.restorePending
                if (step.clearMemory) memory.clear()
                feedFlow?.cancel(); jobsFlow?.cancel(); detailJob?.cancel(); purgeJob?.cancel()
                requestGeneration++; chatGeneration++; activeOperations = 0; cached = emptyList()
                mutable.value = ScreenState(scope = config?.scope.orEmpty())
                draftReady = false; draft = NoteDraftFields(); noteMessage = null; pendingFile = null
                repo = config?.let(container::repository)
                repo?.let { repository ->
                    purgeJob = launch { purgeInactiveScopes(repository) }
                    launch { loadDraft(repository) }
                    feedFlow = launch { repository.cached.collect { rows ->
                        cached = rows
                        mutable.update { it.copy(feed = rows.filter { row -> row.inFeed }.map(repository::decode), library = rows.filter { row -> row.saved }.map(repository::decode)) }
                    } }
                    jobsFlow = launch {
                        var recover = true
                        repository.jobs.collect { jobs ->
                            mutable.update { it.copy(jobs = jobs, notice = noticeAfterJobUpdate(it.notice, jobs)) }
                            if (recover) {
                                recover = false
                                jobs.filter { it.status.lowercase() !in setOf("completed", "complete", "succeeded", "success", "done", "failed", "error", "cancelled", "paused") }
                                    .forEach { IngestWorker.schedule(getApplication(), it.scope, it.id) }
                            }
                        }
                    }
                    if (restore) restoreTutor(repository)
                    refresh()
                }
            }
        }
    }
    /** Another server or token was connected: delete the old cards and completed imports, never unsent note text or drafts. */
    private suspend fun purgeInactiveScopes(repository: LearningRepository) {
        try {
            repository.purgeInactiveScopes { shouldRunPurge(repo?.config?.scope, repository.config.scope) }.forEach { IngestWorker.cancel(getApplication(), it.scope, it.id) }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) { android.util.Log.w("OpenLearning", "Could not purge data from a previous connection") }
    }
    private fun restoreTutor(repository: LearningRepository) {
        val saved = memory.recall(repository.config.scope) ?: return
        viewModelScope.launch {
            val item = resolveInsight(saved.insightId,
                { id -> container.database.dao().get(saved.scope, id)?.let(repository::decode) }, repository.api::insight)
            when (val decision = decideRestore(saved, repository.config.scope, repo === repository, state.value.selected?.id, memory.recall(saved.scope), item)) {
                is RestoreDecision.Restore -> { select(decision.insight); if (saved.learning) markLearning(decision.insight) }
                RestoreDecision.Clear -> memory.clear()
                RestoreDecision.Skip -> Unit
            }
        }
    }
    private fun markLearning(item: Insight) {
        mutable.update { it.copy(learning = true) }
        repo?.let { memory.remember(it.config.scope, item.id, true) }
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
            val row = PendingIngest(repository.config.scope, java.util.UUID.randomUUID().toString(), url)
            container.database.dao().putJob(row)
            IngestWorker.schedule(getApplication(), row.scope, row.id)
        }
    }
    fun retryImport(job: PendingIngest) {
        request { repository ->
            if (job.scope != repository.config.scope) return@request
            if (job.isNoteJob()) { retryNote(repository, job); return@request }
            val retry = if (job.jobId != null && job.status.lowercase() in listOf("failed", "error")) repository.api.retryJob(job.jobId) else null
            container.database.dao().putJob(job.copy(jobId = retry?.id ?: job.jobId, status = retry?.status ?: "queued", error = null))
            IngestWorker.schedule(getApplication(), job.scope, job.id, replace = true)
        }
    }
    private suspend fun retryNote(repository: LearningRepository, job: PendingIngest) {
        val dao = container.database.dao()
        val current = dao.job(job.scope, job.id) ?: return
        val expired = current.copy(status = "failed", error = NOTE_EXPIRED)
        val next = when (val plan = noteRetryPlan(current)) {
            NoteRetry.Expired -> expired
            NoteRetry.Requeue -> current.copy(status = "queued", error = null)
            is NoteRetry.ServerRetry -> try {
                val retry = repository.api.retryJob(plan.jobId)
                current.copy(jobId = retry.id, status = retry.status, error = null)
            } catch (conflict: HttpException) {
                if (!isJobGone(conflict.code())) throw conflict
                noteConflictPlan(current) ?: expired
            }
        }
        // @Update, not REPLACE: a row the user removed meanwhile must stay removed.
        if (dao.updateJob(next) > 0 && next !== expired) IngestWorker.schedule(getApplication(), job.scope, job.id, replace = true)
    }
    private suspend fun loadDraft(repository: LearningRepository) {
        val saved = container.database.dao().draft(repository.config.scope)
        if (repo !== repository) return
        if (saved != null && draft.isEmpty) draft = NoteDraftFields(saved.title, saved.label, saved.text)
        draftReady = true
    }
    private suspend fun saveDraft(fields: NoteDraftFields) {
        val repository = repo ?: return
        val scope = repository.config.scope
        draftLock.withLock {
            // A newer edit or a submit already replaced this value: skip, the next emission covers it.
            if (!draftReady || fields != draft || repo !== repository) return
            val dao = container.database.dao()
            if (fields.isEmpty) dao.deleteDraft(scope) else dao.putDraft(NoteDraft(scope, fields.title, fields.label, fields.text))
        }
    }
    /** Persists the draft now (the editor calls this on stop) instead of waiting for the debounce. */
    fun flushDraft() { viewModelScope.launch { saveDraft(draft) } }
    fun editDraft(next: NoteDraftFields) { draft = next; noteMessage = null }
    fun applyPendingFile() { pendingFile?.let { draft = draft.withFile(it); noteMessage = null }; pendingFile = null }
    fun discardPendingFile() { pendingFile = null }
    fun clearNoteMessage() { noteMessage = null }
    /** Returns true when the note was accepted for queueing, so the editor can close. */
    fun submitNote(): Boolean {
        val repository = repo ?: return false
        val fields = draft
        val note = when (val check = validateNote(fields.title, fields.text, fields.label)) {
            is NoteCheck.Invalid -> { noteMessage = check.message; return false }
            is NoteCheck.Valid -> check.note
        }
        noteMessage = null
        draft = NoteDraftFields()
        viewModelScope.launch {
            try {
                val row = draftLock.withLock { repository.queueNote(note) }
                IngestWorker.schedule(getApplication(), row.scope, row.id)
                val queued = Notice("Note queued. It is sent to your server as soon as it is reachable.", jobId = row.id)
                // The jobs flow may already have reported this note finished; a row not listed yet is not "removed".
                mutable.update { it.copy(notice = if (it.jobs.any { job -> job.id == row.id }) noticeAfterJobUpdate(queued, it.jobs) else queued) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                if (repo === repository && draft.isEmpty) draft = fields
                mutable.update { it.copy(error = "Could not save the note. Your text was kept as a draft.") }
            }
        }
        return true
    }
    /** Reads a picked file into the draft. The Uri is used only inside this call and never stored. */
    fun loadNoteFile(uri: Uri) {
        val resolver = getApplication<Application>().contentResolver
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                var name: String? = null
                var size: Long? = null
                try {
                    resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
                        if (c.moveToFirst()) { name = c.getString(0); if (!c.isNull(1)) size = c.getLong(1) }
                    }
                    readNoteFile(name, size) { resolver.openInputStream(uri) }
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) { NoteFileResult.Rejected("Could not open that file.") }
            }
            when (result) {
                is NoteFileResult.Loaded -> when (filePlan(draft)) {
                    FilePlan.Apply -> { draft = draft.withFile(result); noteMessage = null }
                    FilePlan.ConfirmReplace -> { pendingFile = result; noteMessage = null }
                }
                is NoteFileResult.Rejected -> noteMessage = result.message
            }
        }
    }
    /** Online-only: nothing is cleared locally unless the server confirms the note is gone. */
    fun forgetNote(item: Insight) {
        val repository = repo ?: return
        val scope = repository.config.scope
        if (!item.isNote() || item.documentId.isBlank()) return
        viewModelScope.launch {
            container.forgotten.add(scope, item.documentId)
            try {
                val response = repository.api.forgetNote(item.documentId)
                if (!response.isGone()) throw HttpException(response)
                val purged = repository.purgeDocument(item.documentId)
                if (repo === repository) {
                    memory.clear(); detailJob?.cancel(); chatGeneration++
                    mutable.update { it.withoutDocument(item.documentId, purged + item.id).copy(notice = Notice("Note forgotten.")) }
                }
            } catch (cancelled: CancellationException) { container.forgotten.remove(scope, item.documentId); throw cancelled }
            catch (error: Exception) {
                container.forgotten.remove(scope, item.documentId)
                if (repo === repository) mutable.update { it.copy(error = forgetFailureMessage(error)) }
            }
        }
    }
    /**
     * Cancels the worker and waits (bounded) for it to stop, re-reads the row (it may have just submitted), drops the
     * server job unless another row shares it, then the row. Completed notes are removed by forgetting instead.
     */
    fun removeNoteJob(job: PendingIngest) {
        val repository = repo ?: return
        if (job.scope != repository.config.scope || !job.canRemove()) return
        viewModelScope.launch {
            IngestWorker.cancel(getApplication(), job.scope, job.id)
            IngestWorker.awaitStopped(getApplication(), job.scope, job.id)
            val dao = container.database.dao()
            val current = dao.job(job.scope, job.id) ?: return@launch
            try {
                current.jobId?.let { id ->
                    if (mayDeleteServerJob(id, dao.otherHolders(id, job.scope, job.id))) {
                        val response = repository.api.deleteJob(id)
                        if (!response.isGone()) throw HttpException(response)
                    }
                }
                dao.deleteJob(job.scope, job.id)
                container.database.checkpointBestEffort()
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                val message = forgetFailureMessage(error, "the job was not removed")
                dao.updateJob(current.copy(status = "paused", error = message))
                if (repo === repository) mutable.update { it.copy(error = message) }
            }
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
        memory.remember(repository.config.scope, item.id, false)
        detailJob = viewModelScope.launch {
            try {
                val fresh = repository.api.insight(item.id)
                repository.remember(fresh)
                if (repo === repository && state.value.selected?.id == item.id) mutable.update { it.copy(selected = fresh) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { /* Cached detail remains readable offline. */ }
        }
    }
    fun closeDetail() { memory.clear(); detailJob?.cancel(); chatGeneration++; mutable.update { it.copy(selected = null, chat = emptyList(), chatBusy = false, learning = false) } }
    fun learn(item: Insight) { select(item); markLearning(item); chat("") }
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
        if (repo === repository) mutable.update { it.copy(reviews = it.reviews.filterNot { r -> r.id == review.id }, revealed = false, reviewSource = null, notice = Notice("Next review: ${next.dueAt}")) }
    }
    fun search(query: String) = request { repository ->
        val results = container.forgotten.allowed(repository.config.scope, repository.api.search(query.trim()))
        if (repo === repository) mutable.update { it.copy(results = results, searched = true) }
    }
    fun clearError() { mutable.update { it.copy(error = null, notice = null) } }
}
data class ChatLine(val role: String, val text: String, val question: String = "", val citations: List<Citation> = emptyList())
data class ScreenState(
    val feed: List<Insight> = emptyList(), val library: List<Insight> = emptyList(),
    val results: List<Insight> = emptyList(), val searched: Boolean = false,
    val jobs: List<PendingIngest> = emptyList(), val reviews: List<Review> = emptyList(), val reviewsLoaded: Boolean = false,
    val scope: String = "", val selected: Insight? = null, val learning: Boolean = false, val chat: List<ChatLine> = emptyList(), val chatBusy: Boolean = false,
    val revealed: Boolean = false, val reviewSource: Insight? = null, val busy: Boolean = false, val error: String? = null, val notice: Notice? = null,
)
