package org.openlearning.app

import androidx.room.withTransaction

/** Which server list a refresh replaces. */
enum class CardList { FEED, LIBRARY }

/**
 * The rows a write may put: cards of a hidden document are dropped (a blank document id is never hidden).
 * [replace] FEED marks rows in the feed; LIBRARY marks them saved. Any other write keeps the feed flag of [previous].
 */
fun cardRows(scope: String, items: List<Insight>, hidden: Set<String>, previous: Map<String, CachedInsight>, replace: CardList?): List<CachedInsight> =
    items.filterNot { it.isHiddenBy(hidden) }.map { item ->
        val stored = if (replace == CardList.LIBRARY) item.copy(saved = true) else item
        CachedInsight(scope, item.id, apiGson.toJson(stored), stored.saved, replace == CardList.FEED || previous[item.id]?.inFeed == true)
    }

/**
 * The only writer of CachedInsight. One transaction: the durable forget set (pending, failed and confirmed rows) is read,
 * the replaced list is cleared, the allowed rows are put and, for a list refresh, unreferenced cards are pruned. A response that was in flight
 * while a note was forgotten therefore cannot bring its cards back, however late it lands. Returns the pruned count.
 */
suspend fun LearningRepository.writeCards(items: List<Insight>, replace: CardList? = null): Int {
    val scope = config.scope
    val pruned = database.withTransaction {
        val hidden = dao.hiddenIds(scope).toSet()
        when (replace) { CardList.FEED -> dao.clearFeed(scope); CardList.LIBRARY -> dao.clearSaved(scope); null -> Unit }
        val rows = cardRows(scope, items, hidden, dao.allInsights(scope).associateBy { it.id }, replace)
        if (rows.isNotEmpty()) dao.put(rows)
        if (replace != null) dao.pruneCards(scope) else 0
    }
    if (pruned > 0) database.checkpointBestEffort()
    return pruned
}
