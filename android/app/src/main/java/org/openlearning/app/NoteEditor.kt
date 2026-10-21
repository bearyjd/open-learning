package org.openlearning.app

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.UploadFile
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp

private val NOTE_MIME_TYPES = arrayOf("text/plain", "text/markdown", "text/x-markdown", "application/octet-stream")

/**
 * Fields are backed by the view model's draft (persisted to Room, debounced). Only the "composing"
 * flag in the caller survives process death through saved state, never the text itself.
 * The picker is registered here so a result delivered after process death still reaches the draft.
 */
@Composable
internal fun NoteEditor(model: LearningViewModel, busy: Boolean, onClose: () -> Unit) {
    val draft = model.draft
    val message = model.noteMessage
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) model.loadNoteFile(uri) }
    val overCap = draft.text.codePointCount(0, draft.text.length) > NOTE_MAX_CHARS
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(bottom = 28.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        PageTitle("Write a note.", "Paste or write something you want to learn from. Your server turns it into ideas. It is never linked to a web page.")
        OutlinedTextField(draft.title, { model.editDraft(draft.copy(title = it)) }, Modifier.fillMaxWidth(), label = { Text("Title") }, singleLine = true)
        OutlinedTextField(draft.label, { model.editDraft(draft.copy(label = it)) }, Modifier.fillMaxWidth(), label = { Text("Label (optional)") }, singleLine = true)
        OutlinedTextField(draft.text, { model.editDraft(draft.copy(text = it)) }, Modifier.fillMaxWidth(), label = { Text("Note text") }, minLines = 8, maxLines = 16,
            isError = overCap, supportingText = { Text(noteCounter(draft.text)) })
        message?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        OutlinedButton(onClick = { picker.launch(NOTE_MIME_TYPES) }) {
            Icon(Icons.Default.UploadFile, null); Spacer(Modifier.width(6.dp)); Text("Import a .txt or .md file")
        }
        Text("A file fills the text above so you can review it. Nothing is sent until you add the note.", style = MaterialTheme.typography.bodySmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { if (model.submitNote()) onClose() }, enabled = !busy) {
                Icon(Icons.Default.Add, null); Spacer(Modifier.width(6.dp)); Text("Add note to learning feed")
            }
            TextButton(onClick = onClose) { Text("Close") }
        }
        Text("Your draft is kept on this device until you add or clear it.", style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Default)
    }
}
