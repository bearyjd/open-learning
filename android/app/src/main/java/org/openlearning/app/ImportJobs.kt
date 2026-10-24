package org.openlearning.app

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

@Composable
internal fun ImportJobs(jobs: List<PendingIngest>, model: LearningViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Recent imports", style = MaterialTheme.typography.titleSmall)
        jobs.forEach { job ->
            Surface(shape = RoundedCornerShape(12.dp), color = Color.White) {
                Column(Modifier.fillMaxWidth().padding(12.dp)) {
                    Text(if (job.kind == "discovery") job.discoveryJobLabel() else job.displayName(), maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                    Text(if (job.kind == "discovery") job.discoveryJobStatus() else job.status.replaceFirstChar(Char::uppercase), color = Clay, style = MaterialTheme.typography.labelMedium)
                    (if (job.kind == "discovery") job.discoveryJobError() else job.error)?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                    Row {
                        if (job.canRetry()) TextButton(onClick = { model.retryImport(job) }) { Text("Retry import") }
                        if (job.canRemove()) TextButton(onClick = { model.removeNoteJob(job) }) { Text("Remove note") }
                    }
                }
            }
        }
    }
}

@Composable
internal fun ForgetNote(item: Insight, model: LearningViewModel) {
    var confirming by remember { mutableStateOf(false) }
    OutlinedButton(onClick = { confirming = true }) { Text("Forget this note") }
    if (confirming) AlertDialog(
        onDismissRequest = { confirming = false },
        title = { Text("Forget this note?") },
        text = { Text(FORGET_NOTE_MESSAGE) },
        confirmButton = { TextButton(onClick = { confirming = false; model.forgetNote(item) }) { Text("Forget") } },
        dismissButton = { TextButton(onClick = { confirming = false }) { Text("Cancel") } },
    )
}
