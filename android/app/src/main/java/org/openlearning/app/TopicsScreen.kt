// SPDX-License-Identifier: GPL-3.0-only
package org.openlearning.app

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.platform.InterceptPlatformTextInput
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class, ExperimentalComposeUiApi::class)
@Composable
internal fun TopicsScreen(state: DiscoveryTopicsState, actions: DiscoveryActions, onClose: () -> Unit, privacyEpoch: Long = 0) {
    SecureWindow()
    ExcludeFromAutofill()
    var input by remember(privacyEpoch) { mutableStateOf("") }
    LaunchedEffect(Unit) { actions.loadTopics() }
    DisposableEffect(Unit) { onDispose { actions.closeTopics() } }
    LazyColumn(Modifier.fillMaxSize().testTag("topics-list"), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 28.dp)) {
        item { DiscoveryHeading("Topics") }
        item { Text("Topics come from your private notes and remain in memory only while this screen is open.") }
        item { InterceptPlatformTextInput(NoPersonalizedLearning) {
            OutlinedTextField(input, { input = it }, label = { Text("Topic term") }, singleLine = true,
                keyboardOptions = TextInputPrivacy.noLearningKeyboard, modifier = Modifier.fillMaxWidth().testTag("topic-term"))
        } }
        item { FlowRow {
            DiscoveryButton("Pin topic", { actions.pinTopic(input); input = "" }, input.isNotBlank() && state.pinned.size < 30)
            DiscoveryButton("Refresh", actions::loadTopics, !state.loading)
            DiscoveryButton("Close", onClose)
        } }
        item { DiscoveryFeedback(state.error, state.message, actions) }
        if (state.loading) item { CircularProgressIndicator() }
        item { Text("Pinned topics (${state.pinned.size}/30)", modifier = Modifier.semantics { heading() }) }
        items(state.pinned, key = { "pin-${it.id}" }) { pin ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(pin.term, modifier = Modifier.weight(1f).padding(vertical = 12.dp))
                DiscoveryButton("Unpin", { actions.unpinTopic(pin) }, pin.id !in state.operating)
            }
        }
        item { Text("From your notes", modifier = Modifier.semantics { heading() }) }
        if (state.loaded && state.topics.isEmpty()) item { Text("No derived topics yet.") }
        itemsIndexed(state.topics, key = { index, _ -> "topic-$index" }) { _, topic ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(topic.term, modifier = Modifier.weight(1f).padding(vertical = 12.dp))
                DiscoveryButton("Pin", { actions.pinTopic(topic.term) }, state.pinned.size < 30 && state.pinned.none { it.term == topic.term })
            }
        }
    }
}
