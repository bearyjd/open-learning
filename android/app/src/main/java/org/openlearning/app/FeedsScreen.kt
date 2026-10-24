// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.platform.InterceptPlatformTextInput
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class, ExperimentalComposeUiApi::class)
@Composable
internal fun FeedsScreen(state: DiscoveryFeedsState, actions: DiscoveryActions, onClose: () -> Unit, privacyEpoch: Long = 0) {
    SecureWindow()
    ExcludeFromAutofill()
    var input by remember(privacyEpoch) { mutableStateOf("") }
    var confirming by remember(privacyEpoch) { mutableStateOf<DiscoveryFeed?>(null) }
    LaunchedEffect(Unit) { actions.loadFeeds() }
    DisposableEffect(Unit) { onDispose { actions.closeFeeds() } }
    confirming?.let { feed -> AlertDialog(
        onDismissRequest = { confirming = null },
        title = { Text("Remove this feed?") },
        text = { Text("This stops future fetches from this feed.") },
        confirmButton = { DiscoveryButton("Remove", { confirming = null; actions.removeFeed(feed) }) },
        dismissButton = { DiscoveryButton("Cancel", { confirming = null }) },
    ) }
    LazyColumn(Modifier.fillMaxSize().testTag("feeds-list"), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 28.dp)) {
        item { DiscoveryHeading("Feeds") }
        item { InterceptPlatformTextInput(NoPersonalizedLearning) {
            OutlinedTextField(input, { input = it }, label = { Text("Feed URL") }, singleLine = true,
                keyboardOptions = TextInputPrivacy.credentialKeyboard(KeyboardType.Uri), modifier = Modifier.fillMaxWidth().testTag("feed-url"))
        } }
        item { FlowRow {
            DiscoveryButton("Add feed", { actions.addFeed(input); input = "" }, input.isNotBlank() && !state.loading)
            DiscoveryButton("Refresh", actions::loadFeeds, !state.loading)
            DiscoveryButton("Close", onClose)
        } }
        item { DiscoveryFeedback(state.error, state.message, actions) }
        if (state.loading && state.items.isEmpty()) item { CircularProgressIndicator() }
        if (state.loaded && state.items.isEmpty()) item { Text("No feeds yet. Add an RSS or Atom feed URL.") }
        items(state.items, key = { it.id }) { feed -> OutlinedCard(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(feed.title.ifBlank { discoveryHost(feed.url) }, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
                Text(discoveryHost(feed.url))
                Text(feed.lastFetchedAt?.let { discoveryPublished(it)?.let { ago -> "Last fetched: ${if (ago == "Just published") "just now" else ago}" } } ?: "Not fetched yet")
                if (feed.paused) Text("Feed paused")
                feed.lastError?.let { Text(feedErrorMessage(it), color = MaterialTheme.colorScheme.error) }
                FlowRow {
                    DiscoveryButton("Refresh now", { actions.refreshFeed(feed) }, feed.id !in state.operating)
                    DiscoveryButton("Remove", { confirming = feed }, feed.id !in state.operating)
                }
            }
        } }
    }
}

