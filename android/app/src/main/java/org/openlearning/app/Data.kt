package org.openlearning.app

import android.app.Application
import android.content.Context
import androidx.room.*
import com.google.gson.Gson
import com.google.gson.FieldNamingPolicy
import com.google.gson.GsonBuilder
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import okhttp3.OkHttpClient
import retrofit2.HttpException
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
)
data class IngestJob(val id: String, val status: String, val error: String? = null)
data class UrlRequest(val url: String)
data class Feedback(val action: String = "dismiss")
data class ChatRequest(val insightId: String, val message: String)
data class Citation(val documentId: String = "", val sourceUrl: String = "", val passage: String = "")
data class ChatReply(val answer: String = "", val question: String = "", val citations: List<Citation> = emptyList(), val reviewId: String = "")
data class Review(val id: String, val insightId: String, val question: String, val dueAt: String, val intervalDays: Double, val insightTitle: String = "")
data class Rating(val rating: String)

interface LearningApi {
    @GET("api/v1/feed/today") suspend fun feed(): List<Insight>
    @GET("api/v1/library") suspend fun library(): List<Insight>
    @GET("api/v1/insights/{id}") suspend fun insight(@Path("id") id: String): Insight
    @POST("api/v1/insights/{id}/save") suspend fun save(@Path("id") id: String): Insight
    @POST("api/v1/insights/{id}/feedback") suspend fun dismiss(@Path("id") id: String, @Body body: Feedback = Feedback())
    @POST("api/v1/ingest/url") suspend fun ingest(@Body body: UrlRequest): IngestJob
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
data class PendingIngest(val scope: String, val id: String, val url: String, val jobId: String? = null, val status: String = "queued", val error: String? = null)
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
    @Query("SELECT * FROM PendingIngest WHERE scope = :scope ORDER BY rowid DESC LIMIT 12") fun jobs(scope: String): Flow<List<PendingIngest>>
}
@Database(entities = [CachedInsight::class, PendingIngest::class], version = 1, exportSchema = true)
abstract class LearningDatabase : RoomDatabase() { abstract fun dao(): LearningDao }

class LearningRepository(val config: Connection, private val database: LearningDatabase) {
    private val gson: Gson = GsonBuilder().setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES).create()
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
        val previous = dao.get(config.scope, insight.id)
        dao.put(listOf(CachedInsight(config.scope, insight.id, gson.toJson(insight), insight.saved, inFeed ?: previous?.inFeed ?: false)))
    }
    suspend fun refreshFeed() {
        val items = api.feed()
        database.withTransaction {
            dao.clearFeed(config.scope)
            for (item in items) remember(item, true)
        }
    }
    suspend fun refreshLibrary() {
        val items = api.library()
        database.withTransaction {
            dao.clearSaved(config.scope)
            for (item in items) remember(item.copy(saved = true))
        }
    }
    suspend fun save(item: Insight) { remember(api.save(item.id)) }
    suspend fun dismiss(item: Insight) { api.dismiss(item.id); dao.dismiss(config.scope, item.id) }
}
class AppContainer(context: Context) {
    val credentials = Credentials(context)
    val database = Room.databaseBuilder(context, LearningDatabase::class.java, "learning.db").build()
    fun repository(connection: Connection) = LearningRepository(connection, database)
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
