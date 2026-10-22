package org.openlearning.app

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/** "Your notes": titles and labels come from the server on each open and live in memory only, never in Room. */
@Composable
internal fun NotesList(state: NotesListState, forgetWaiting: Int, forgetFailed: List<PendingForget>, model: LearningViewModel, onClose: () -> Unit) {
    SecureWindow()
    LaunchedEffect(Unit) { model.loadNotes() }
    // Titles leave memory when the screen closes (back, Close, tab switch, rotation, scope change).
    DisposableEffect(Unit) { onDispose { model.closeNotes() } }
    var confirming by remember { mutableStateOf<NoteSummary?>(null) }
    confirming?.let { note ->
        AlertDialog(
            onDismissRequest = { confirming = null },
            title = { Text("Forget this note?") },
            text = { Text(FORGET_NOTE_MESSAGE) },
            confirmButton = { TextButton(onClick = { confirming = null; model.forgetListedNote(note) }) { Text("Forget") } },
            dismissButton = { TextButton(onClick = { confirming = null }) { Text("Cancel") } },
        )
    }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 28.dp)) {
        item { PageTitle("Your notes", "The notes on your server. This list is only kept while this screen is open.") }
        state.message?.let { message -> item { StatusMessage(message, false, model::noteMessageShown) } }
        item { Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = model::loadNotes, enabled = !state.loading) { Text("Refresh") }
            TextButton(onClick = onClose) { Text("Close") }
        } }
        forgetWaitingLine(forgetWaiting)?.let { line -> item(key = "forget-waiting") { Text(line, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) } }
        items(forgetFailed, key = { "forget-failed-${it.documentId}" }) { row -> FailedForgetRow(row, { model.retryForget(row.documentId) }) { model.stopTryingForget(row.documentId) } }
        when (notesBody(state)) {
            NotesBody.Loading -> item { CircularProgressIndicator(Modifier.padding(vertical = 24.dp)) }
            NotesBody.Error -> item { EmptyState("Couldn't load your notes", state.error.orEmpty()) }
            NotesBody.Empty -> item { EmptyState("No notes yet", "Notes you add appear here once your server has them.") }
            NotesBody.List -> {
                state.error?.let { error -> item { Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) } }
                items(state.items, key = { it.documentId }) { note -> NoteRow(note, note.documentId in state.forgetting) { confirming = note } }
            }
        }
    }
}

@Composable
private fun NoteRow(note: NoteSummary, forgetting: Boolean, onForget: () -> Unit) {
    OutlinedCard(Modifier.fillMaxWidth(), colors = CardDefaults.outlinedCardColors(containerColor = Color.White), shape = RoundedCornerShape(18.dp)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(note.title, style = MaterialTheme.typography.titleMedium, fontFamily = FontFamily.Serif, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(note.rowMeta(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onForget, enabled = !forgetting) { Text(if (forgetting) "Forgetting…" else "Forget") }
            }
        }
    }
}

/** A forget that failed: the note stays hidden here. Fixed text only, never a title. Retry or Stop trying (after a confirm). */
@Composable
private fun FailedForgetRow(row: PendingForget, onRetry: () -> Unit, onStop: () -> Unit) {
    var confirming by remember { mutableStateOf(false) }
    if (confirming) AlertDialog(
        onDismissRequest = { confirming = false },
        title = { Text("Stop trying to delete?") },
        text = { Text(FORGET_STOP_MESSAGE) },
        confirmButton = { TextButton(onClick = { confirming = false; onStop() }) { Text("Stop trying") } },
        dismissButton = { TextButton(onClick = { confirming = false }) { Text("Cancel") } },
    )
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
        Text(forgetFailedLine(row), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, modifier = Modifier.weight(1f))
        TextButton(onClick = onRetry) { Text("Retry") }
        TextButton(onClick = { confirming = true }, enabled = canStopTrying(row)) { Text("Stop trying") }
    }
}
