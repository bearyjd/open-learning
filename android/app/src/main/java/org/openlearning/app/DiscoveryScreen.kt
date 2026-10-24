// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.net.URI
import java.time.Instant
import java.time.Duration

/** Only the route is saved. All fetched content and input fields are memory-only. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun DiscoveryScreen(model: LearningViewModel, scope: String, jobs: List<PendingIngest>, onClose: () -> Unit) {
    SecureWindow()
    val state by model.discovery.collectAsStateWithLifecycle()
    val actions = model.discoveryActions
    var section by rememberSaveable { mutableStateOf("queue") }
    DisposableEffect(Unit) { onDispose { actions.clear() } }
    BackHandler(section != "queue") { section = "queue" }
    Column {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            DiscoveryButton("Reading queue", { section = "queue" })
            DiscoveryButton("Feeds", { section = "feeds" })
            DiscoveryButton("Topics", { section = "topics" })
        }
        // A scope replacement must dispose private inputs and confirmation references even on the same route.
        key(scope) {
            when (section) {
                "feeds" -> FeedsScreen(state.feeds, actions, onClose, state.epoch)
                "topics" -> TopicsScreen(state.topics, actions, onClose, state.epoch)
                else -> ReadingQueue(state.queue, actions, jobs, model, onClose)
            }
        }
    }
}

@Composable
internal fun DiscoveryButton(label: String, onClick: () -> Unit, enabled: Boolean = true) {
    TextButton(onClick = onClick, enabled = enabled, modifier = Modifier.heightIn(min = 48.dp)) { Text(label) }
}

@Composable
internal fun DiscoveryHeading(title: String) {
    Text(title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() })
}

@Composable
internal fun DiscoveryFeedback(error: String?, message: String?, actions: DiscoveryActions) {
    error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }) }
    message?.let { StatusMessage(it, false, actions::messageShown) }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun ReadingQueue(state: DiscoveryQueueState, actions: DiscoveryActions, jobs: List<PendingIngest>, model: LearningViewModel, onClose: () -> Unit) {
    LaunchedEffect(Unit) { actions.loadQueue() }
    DisposableEffect(Unit) { onDispose { actions.closeQueue() } }
    PullToRefreshBox(isRefreshing = state.loading, onRefresh = { actions.loadQueue() }, modifier = Modifier.fillMaxSize()) {
        LazyColumn(Modifier.fillMaxSize().testTag("reading-queue"), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 28.dp)) {
            item { DiscoveryHeading("Reading queue") }
            item { FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DiscoverySort.entries.forEach { sort -> FilterChip(
                    selected = state.sort == sort, onClick = { actions.sortQueue(sort) },
                    label = { Text(if (sort == DiscoverySort.NEWEST) "Newest" else "Relevance") },
                    modifier = Modifier.heightIn(min = 48.dp),
                ) }
            } }
            item { FlowRow { DiscoveryButton("Refresh", { actions.loadQueue() }, !state.loading); DiscoveryButton("Close", onClose) } }
            item { DiscoveryFeedback(state.error, state.message, actions) }
            if (jobs.isNotEmpty()) item { ImportJobs(jobs, model) }
            if (state.loading && state.items.isEmpty()) item { CircularProgressIndicator() }
            if (state.loaded && state.items.isEmpty()) item { Text("Your reading queue is empty.") }
            items(state.items, key = { it.id }) { entry ->
                OutlinedCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(entry.title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
                        Text(discoveryHost(entry.url), style = MaterialTheme.typography.labelMedium)
                        Text(entry.snippet, maxLines = 4)
                        discoveryPublished(entry.publishedAt)?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                        if (state.sort == DiscoverySort.RELEVANCE) {
                            entry.matchedTopics.take(5).forEach { topic -> SuggestionChip(onClick = {}, label = { Text(topic) }, enabled = false, modifier = Modifier.heightIn(min = 48.dp)) }
                        }
                        FlowRow {
                            DiscoveryButton("Ingest", { actions.ingest(entry) }, entry.id !in state.operating)
                            DiscoveryButton("Dismiss", { actions.dismiss(entry) }, entry.id !in state.operating)
                        }
                        discoveryExternalUrl(entry.url)?.let { SourceLink(it) }
                    }
                }
            }
            if (state.nextCursor != null) item { DiscoveryButton("Load more", actions::nextPage, !state.loading) }
        }
    }
}

/** Browser destinations accept only ordinary HTTP(S) URLs without embedded credentials. */
internal fun discoveryExternalUrl(input: String): String? = runCatching {
    val uri = URI(input)
    input.takeIf { uri.scheme?.lowercase() in setOf("http", "https") && uri.host != null && uri.userInfo == null }
}.getOrNull()

internal fun discoveryHost(input: String): String = runCatching { URI(input).host }.getOrNull().orEmpty().ifBlank { "Source" }

internal fun discoveryPublished(value: String?): String? = value?.let {
    runCatching {
        val elapsed = Duration.between(Instant.parse(it), Instant.now()).seconds.coerceAtLeast(0)
        when {
            elapsed < 60 -> "Just published"
            elapsed < 3600 -> "${elapsed / 60} minutes ago"
            elapsed < 86400 -> "${elapsed / 3600} hours ago"
            else -> "${elapsed / 86400} days ago"
        }
    }.getOrNull()
}
