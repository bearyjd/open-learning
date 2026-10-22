package org.openlearning.app

import android.app.Application
import android.content.Context
import androidx.room.*
import com.google.gson.Gson
import com.google.gson.FieldNamingPolicy
import com.google.gson.GsonBuilder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.*
import retrofit2.http.Query as HttpQuery
import java.io.IOException
import java.util.concurrent.TimeUnit

// All text is rendered as text, including extracted source passages and model responses.
data class Insight(
    val id: String = "", val title: String = "", val idea: String = "",
    val explanation: String = "", val whyItMatters: String = "", val sourceTitle: String = "",
    val sourceUrl: String = "", val documentId: String = "", val evidence: String = "",
    val tags: List<String> = emptyList(), val saved: Boolean = false,
    val coverage: String = "", val sourceTruncated: Boolean = false, val model: String = "",
    val sourceKind: String = "url", val sourceLabel: String = "",
)
data class IngestJob(val id: String, val status: String, val error: String? = null, val documentId: String? = null)
data class UrlRequest(val url: String)
// A null label is omitted by Gson; the server rejects explicit nulls.
data class TextRequest(val title: String, val text: String, val label: String? = null) {
    override fun toString() = "TextRequest(text=<redacted>)"
}
data class Feedback(val action: String = "dismiss")
data class ChatRequest(val insightId: String, val message: String)
data class Citation(val documentId: String = "", val sourceUrl: String = "", val passage: String = "",
    val sourceKind: String = "url", val sourceLabel: String = "")
data class ChatReply(val answer: String = "", val question: String = "", val citations: List<Citation> = emptyList(), val reviewId: String = "")
data class Review(val id: String, val insightId: String, val question: String, val dueAt: String, val intervalDays: Double, val insightTitle: String = "")
data class Rating(val rating: String)

/** DELETE succeeded, or the server already has nothing to delete. */
fun Response<Unit>.isGone() = isSuccessful || code() == 404

interface LearningApi {
    @GET("api/v1/feed/today") suspend fun feed(): List<Insight>
    @GET("api/v1/library") suspend fun library(): List<Insight>
    @GET("api/v1/insights/{id}") suspend fun insight(@Path("id") id: String): Insight
    @POST("api/v1/insights/{id}/save") suspend fun save(@Path("id") id: String): Insight
    @POST("api/v1/insights/{id}/feedback") suspend fun dismiss(@Path("id") id: String, @Body body: Feedback = Feedback())
    @POST("api/v1/ingest/url") suspend fun ingest(@Body body: UrlRequest): IngestJob
    @POST("api/v1/ingest/text") suspend fun ingestText(@Body body: TextRequest): IngestJob
    @GET("api/v1/notes") suspend fun notes(@HttpQuery("limit") limit: Int = NOTES_PAGE_LIMIT, @HttpQuery("offset") offset: Int = 0): List<NoteListItem>
    @GET("api/v1/limits") suspend fun limits(): Limits
    @DELETE("api/v1/notes/{id}") suspend fun forgetNote(@Path("id") documentId: String): Response<Unit>
    @DELETE("api/v1/jobs/{id}") suspend fun deleteJob(@Path("id") id: String): Response<Unit>
    @GET("api/v1/jobs/{id}") suspend fun job(@Path("id") id: String): IngestJob
    @POST("api/v1/jobs/{id}/retry") suspend fun retryJob(@Path("id") id: String): IngestJob
    @GET("api/v1/search") suspend fun search(@HttpQuery("q") query: String): List<Insight>
    @POST("api/v1/tutor/chat") suspend fun chat(@Body body: ChatRequest): ChatReply
    @GET("api/v1/reviews/due") suspend fun reviews(): List<Review>
    @POST("api/v1/reviews/{id}/answer") suspend fun rate(@Path("id") id: String, @Body body: Rating): Review
}

@Entity(primaryKeys = ["scope", "id"])
data class CachedInsight(val scope: String, val id: String, val payload: String, val saved: Boolean, val inFeed: Boolean)
@Entity(primaryKeys = ["scope", "id"])
data class PendingIngest(
    val scope: String, val id: String, val url: String, val jobId: String? = null, val status: String = "queued", val error: String? = null,
    @ColumnInfo(defaultValue = "url") val kind: String = "url",
    val title: String? = null, val label: String? = null, val text: String? = null,
    // Set from the server job when a note job completes; null for rows made before schema v3.
    val documentId: String? = null,
) {
    // Note bodies are private: keep them out of logs and crash reports.
    override fun toString() = "PendingIngest(scope=$scope, id=$id, kind=$kind, jobId=$jobId, status=$status, text=${if (text == null) "null" else "<redacted>"})"
}
/** Reserved for offline forget (E2b-3); nothing reads or writes it yet. */
@Entity(primaryKeys = ["scope", "documentId"])
data class PendingForget(val scope: String, val documentId: String, val createdAt: Long, val status: String, val error: String? = null)
@Entity
data class NoteDraft(@PrimaryKey val scope: String, val title: String, val label: String, val text: String) {
    override fun toString() = "NoteDraft(scope=$scope, text=<redacted>)"
}
@Dao
interface LearningDao {
    @Query("SELECT * FROM CachedInsight WHERE scope = :scope AND (saved = 1 OR inFeed = 1)")
    fun observe(scope: String): Flow<List<CachedInsight>>
    @Query("SELECT * FROM CachedInsight WHERE scope = :scope AND id = :id")
    suspend fun get(scope: String, id: String): CachedInsight?
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun put(rows: List<CachedInsight>)
    @Query("UPDATE CachedInsight SET inFeed = 0 WHERE scope = :scope") suspend fun clearFeed(scope: String)
    @Query("UPDATE CachedInsight SET saved = 0 WHERE scope = :scope") suspend fun clearSaved(scope: String)
    @Query("UPDATE CachedInsight SET inFeed = 0 WHERE scope = :scope AND id = :id") suspend fun dismiss(scope: String, id: String)
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun putJob(job: PendingIngest)
    @Query("SELECT * FROM PendingIngest WHERE scope = :scope AND id = :id") suspend fun job(scope: String, id: String): PendingIngest?
    // Newest 12 rows, plus every row still holding note text so a queued note is never hidden.
    @Query("SELECT * FROM PendingIngest WHERE scope = :scope AND (text IS NOT NULL OR rowid IN (SELECT rowid FROM PendingIngest WHERE scope = :scope ORDER BY rowid DESC LIMIT 12)) ORDER BY rowid DESC")
    fun jobs(scope: String): Flow<List<PendingIngest>>
    // Returns the rows changed: 0 means the user removed the job, so it must not be resurrected.
    @Update suspend fun updateJob(job: PendingIngest): Int
    @Query("DELETE FROM PendingIngest WHERE scope = :scope AND id = :id") suspend fun deleteJob(scope: String, id: String)
    // How many other rows reference this server job (the server dedups by hash, so ids can be shared).
    @Query("SELECT COUNT(*) FROM PendingIngest WHERE jobId = :jobId AND NOT (scope = :scope AND id = :id)")
    suspend fun otherHolders(jobId: String, scope: String, id: String): Int
    // Completed notes hold no text but still show a title; forgetting must not leave them behind.
    @Query("DELETE FROM PendingIngest WHERE scope = :scope AND kind = 'note' AND text IS NULL AND lower(status) IN (:states)")
    suspend fun deleteCompletedNotes(scope: String, states: List<String>): Int
    @Query("SELECT * FROM PendingIngest WHERE scope = :scope AND kind = 'note' AND text IS NULL AND lower(status) IN (:states)")
    suspend fun completedNoteRows(scope: String, states: List<String>): List<PendingIngest>
    @Query("DELETE FROM CachedInsight WHERE scope = :scope AND saved = 0 AND inFeed = 0") suspend fun pruneCards(scope: String): Int
    @Query("DELETE FROM CachedInsight WHERE scope != :scope") suspend fun deleteCardsOutside(scope: String): Int
    @Query("SELECT * FROM PendingIngest WHERE scope != :scope") suspend fun jobsOutside(scope: String): List<PendingIngest>
    @Query("SELECT * FROM NoteDraft WHERE scope = :scope") suspend fun draft(scope: String): NoteDraft?
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun putDraft(draft: NoteDraft)
    @Query("DELETE FROM NoteDraft WHERE scope = :scope") suspend fun deleteDraft(scope: String)
    @Query("SELECT * FROM CachedInsight WHERE scope = :scope") suspend fun allInsights(scope: String): List<CachedInsight>
    @Query("DELETE FROM CachedInsight WHERE scope = :scope AND id IN (:ids)") suspend fun deleteInsights(scope: String, ids: List<String>)
}
@Database(entities = [CachedInsight::class, PendingIngest::class, NoteDraft::class, PendingForget::class], version = 3, exportSchema = true)
abstract class LearningDatabase : RoomDatabase() { abstract fun dao(): LearningDao }

// Overwrite deleted pages and flush the WAL so cleared note text does not linger on disk.
// Cursors are lazy: the statement only runs once the cursor is stepped, so step it before closing.
val SecureDeleteCallback = object : RoomDatabase.Callback() {
    override fun onOpen(db: androidx.sqlite.db.SupportSQLiteDatabase) { db.query("PRAGMA secure_delete=ON").use { it.moveToFirst() } }
}
const val CHECKPOINT_FAILED = "WAL checkpoint did not complete"
/** wal_checkpoint returns (busy, log, checkpointed); busy 0 means the whole WAL was folded in and truncated. */
fun checkpointSucceeded(busy: Int?) = busy == 0
/**
 * Truncates the WAL (shortens the file, it does not overwrite it). Throws a fixed message when a reader keeps
 * the checkpoint busy. Never run it inside a transaction. Runs on the IO dispatcher: it blocks on disk and locks.
 */
suspend fun LearningDatabase.checkpoint() = withContext(Dispatchers.IO) {
    openHelper.writableDatabase.query("PRAGMA wal_checkpoint(TRUNCATE)").use {
        check(checkpointSucceeded(if (it.moveToFirst()) it.getInt(0) else null)) { CHECKPOINT_FAILED }
    }
}
/** For callers whose real work already succeeded: log a fixed string (never row text) and carry on. */
suspend fun LearningDatabase.checkpointBestEffort() {
    try { checkpoint() } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled } catch (error: Exception) { android.util.Log.w("OpenLearning", CHECKPOINT_FAILED) }
}

val apiGson: Gson = GsonBuilder().setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES).create()

class LearningRepository(val config: Connection, private val database: LearningDatabase, private val forgotten: ForgottenNotes = ForgottenNotes()) {
    private val gson: Gson = apiGson
    private val dao = database.dao()
    val api: LearningApi = Retrofit.Builder().baseUrl(config.server)
        .client(OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
            .connectTimeout(15, TimeUnit.SECONDS).readTimeout(90, TimeUnit.SECONDS)
            .addInterceptor { chain -> chain.proceed(chain.request().newBuilder().header("Authorization", "Bearer ${config.token}").build()) }.build())
        .addConverterFactory(GsonConverterFactory.create(gson)).build().create(LearningApi::class.java)
    val cached: Flow<List<CachedInsight>> = dao.observe(config.scope)
    val jobs = dao.jobs(config.scope)
    fun decode(row: CachedInsight): Insight = gson.fromJson(row.payload, Insight::class.java).copy(saved = row.saved)
    suspend fun remember(insight: Insight, inFeed: Boolean? = null) {
        if (forgotten.blocks(config.scope, insight)) return
        val previous = dao.get(config.scope, insight.id)
        dao.put(listOf(CachedInsight(config.scope, insight.id, gson.toJson(insight), insight.saved, inFeed ?: previous?.inFeed ?: false)))
    }
    suspend fun refreshFeed() {
        val items = api.feed()
        val dropped = database.withTransaction {
            dao.clearFeed(config.scope)
            for (item in items) remember(item, true)
            dao.pruneCards(config.scope)
        }
        if (dropped > 0) database.checkpointBestEffort()
    }
    suspend fun refreshLibrary() {
        val items = api.library()
        val dropped = database.withTransaction {
            dao.clearSaved(config.scope)
            for (item in items) remember(item.copy(saved = true))
            dao.pruneCards(config.scope)
        }
        if (dropped > 0) database.checkpointBestEffort()
    }
    /** One transaction: queue the note and drop the draft, so a crash never loses or duplicates it. */
    suspend fun queueNote(note: ValidNote): PendingIngest {
        val row = PendingIngest(config.scope, java.util.UUID.randomUUID().toString(), "", kind = "note", title = note.title, label = note.label, text = note.text)
        database.withTransaction { dao.putJob(row); dao.deleteDraft(config.scope) }
        database.checkpointBestEffort()
        return row
    }
    /**
     * Deletes every cached card of this scope that belongs to the document, scanning all rows (not the feed/library
     * filter), and only the completed, text-free import rows linked to that document ([purgeNoteLinkedRows]).
     */
    suspend fun purgeDocument(documentId: String): List<String> {
        val ids = database.withTransaction {
            val found = dao.allInsights(config.scope).filter { decode(it).documentId == documentId }.map { it.id }
            if (found.isNotEmpty()) dao.deleteInsights(config.scope, found)
            deleteNoteRows(documentId)
            found
        }
        database.checkpointBestEffort()
        return ids
    }
    /** Targeted purge of the completed import rows of one note; see [noteRowsToPurge] for the pre-migration fallback. */
    suspend fun purgeNoteLinkedRows(documentId: String): Int {
        val removed = database.withTransaction { deleteNoteRows(documentId) }
        database.checkpointBestEffort()
        return removed
    }
    private suspend fun deleteNoteRows(documentId: String): Int {
        val rows = noteRowsToPurge(dao.completedNoteRows(config.scope, COMPLETED_STATES), documentId)
        rows.forEach { dao.deleteJob(it.scope, it.id) }
        return rows.size
    }
    /**
     * Drops the cards and the completed, text-free import rows of other scopes. Unsent note text (queued, failed,
     * waiting or paused rows) and note drafts of other scopes are never deleted: they stay hidden until that account
     * is connected again. Does nothing when [isCurrent] says this connection is no longer the active one (A to B to A).
     * Returns the removed rows so their work can be cancelled.
     */
    suspend fun purgeInactiveScopes(isCurrent: () -> Boolean = { true }): List<PendingIngest> {
        val stale = database.withTransaction {
            if (!isCurrent()) return@withTransaction emptyList()
            val rows = dao.jobsOutside(config.scope).filter { it.purgeableAfterScopeChange() }
            dao.deleteCardsOutside(config.scope)
            rows.forEach { dao.deleteJob(it.scope, it.id) }
            rows
        }
        database.checkpointBestEffort()
        return stale
    }
    suspend fun save(item: Insight) { remember(api.save(item.id)) }
    suspend fun dismiss(item: Insight) { api.dismiss(item.id); dao.dismiss(config.scope, item.id) }
}
class AppContainer(context: Context) {
    val credentials = Credentials(context)
    val database = Room.databaseBuilder(context, LearningDatabase::class.java, "learning.db")
        .addMigrations(MIGRATION_1_2, MIGRATION_2_3).addCallback(SecureDeleteCallback).build()
    /** Notes being forgotten: in-flight refreshes must not write their cards back. */
    val forgotten = ForgottenNotes()
    fun repository(connection: Connection) = LearningRepository(connection, database, forgotten)
}
class LearningApplication : Application() {
    val container by lazy { AppContainer(this) }
}
fun Throwable.userMessage(): String = when (this) {
    is HttpException -> when (code()) {
        401, 403 -> "Access denied. Check your server token in Connection."
        404 -> "This server does not expose the expected Open Learning API."
        429 -> "The server is busy. Try again shortly."
        in 500..599 -> "The server could not complete this request. Try again."
        else -> "Request failed (HTTP ${code()})."
    }
    is IOException -> "Cannot reach the server. Check your connection. Your saved library is available offline."
    else -> "Could not complete this request. Please try again."
}
