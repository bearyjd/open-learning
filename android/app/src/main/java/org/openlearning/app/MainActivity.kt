package org.openlearning.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.InterceptPlatformTextInput
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

class MainActivity : ComponentActivity() {
    private var shared by mutableStateOf<String?>(null)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        receive(intent)
        setContent { LearningTheme { LearningApp(shared, consumeShare = { shared = null }) } }
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); receive(intent) }
    private fun receive(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") shared = intent.getStringExtra(Intent.EXTRA_TEXT)
    }
}
internal val Ink = Color(0xFF173A36)
private val Paper = Color(0xFFFAF8F2)
internal val Clay = Color(0xFFAF613B)
@Composable private fun LearningTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = lightColorScheme(primary = Ink, onPrimary = Color.White, secondary = Clay,
        background = Paper, surface = Paper, surfaceVariant = Color(0xFFEAEFE8), onSurface = Color(0xFF202C29)), content = content)
}
private enum class Destination(val title: String, val icon: ImageVector) {
    Discover("Discover", Icons.Default.Explore), Library("Library", Icons.Default.Bookmarks),
    Learn("Learn", Icons.Default.School), Review("Review", Icons.Default.Refresh), Explore("Explore", Icons.Default.Search)
}
@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun LearningApp(shared: String?, consumeShare: () -> Unit, model: LearningViewModel = viewModel()) {
    val state by model.state.collectAsStateWithLifecycle()
    val connection by model.connection.collectAsStateWithLifecycle()
    var destination by rememberSaveable { mutableStateOf(Destination.Discover) }
    var settings by rememberSaveable { mutableStateOf(false) }
    var importText by rememberSaveable { mutableStateOf("") }
    // Only this flag survives process death; the note text is a Room-backed draft in the view model.
    var composingNote by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(shared) { if (shared != null) { importText = sharedUrl(shared) ?: shared; destination = Destination.Discover; composingNote = false; model.closeDetail(); consumeShare() } }
    LaunchedEffect(destination, connection?.scope) { if (destination == Destination.Review && connection != null) model.loadReviews() }
    BackHandler(state.selected != null) { model.closeDetail() }
    BackHandler(composingNote && state.selected == null) { composingNote = false }
    if (settings || connection == null) ConnectionDialog(connection, model, onDismiss = { settings = false })
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val expanded = maxWidth >= 840.dp
        Scaffold(
            topBar = { TopAppBar(title = { Column {
                Text("OPEN LEARNING", fontSize = 13.sp, letterSpacing = 2.sp, fontWeight = FontWeight.Bold)
                Text(connection?.server?.removePrefix("https://")?.removePrefix("http://")?.trimEnd('/') ?: "Your curiosity, connected", style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            } }, navigationIcon = { if (state.selected != null) IconButton(onClick = model::closeDetail) { Icon(Icons.Default.ArrowBack, "Back") } },
                actions = { IconButton(onClick = { settings = true }) { Icon(Icons.Default.Settings, "Connection settings") } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Paper)) },
            bottomBar = { if (!expanded) NavigationBar(containerColor = Paper) {
                Destination.entries.forEach { entry -> NavigationBarItem(selected = destination == entry, onClick = { destination = entry; model.closeDetail() }, icon = { Icon(entry.icon, entry.title) }, label = { Text(entry.title) }) }
            } }
        ) { padding ->
            Row(Modifier.padding(padding).fillMaxSize()) {
                if (expanded) NavigationRail(containerColor = Paper, modifier = Modifier.fillMaxHeight()) {
                    Spacer(Modifier.height(24.dp))
                    Destination.entries.forEach { entry -> NavigationRailItem(selected = destination == entry, onClick = { destination = entry; model.closeDetail() }, icon = { Icon(entry.icon, entry.title) }, label = { Text(entry.title) }) }
                }
                Column(Modifier.weight(1f).fillMaxHeight().padding(horizontal = if (expanded) 32.dp else 20.dp)) {
                    if (state.busy || state.chatBusy) LinearProgressIndicator(Modifier.fillMaxWidth())
                    state.error?.let { StatusMessage(it, true, model::clearError) }
                    state.notice?.let { StatusMessage(it, false, model::clearError) }
                    if (connection == null) EmptyState("A home for what you learn", "Connect your own Open Learning server to collect useful ideas, explore their sources, and make them stick.")
                    else if (state.selected != null) {
                        if (state.learning) Tutor(state, model) else InsightDetail(state.selected!!, model)
                    } else when (destination) {
                        Destination.Discover -> if (composingNote) NoteEditor(model, state.busy) { composingNote = false }
                            else Discover(state, importText, { importText = it }, { model.importUrl(importText); importText = "" }, { composingNote = true }, model)
                        Destination.Library -> InsightList("Your library", "Keep the ideas you want to return to. Saved ideas stay readable offline.", state.library, "No saved ideas yet", "Save an idea from Discover to keep its explanation and source here.", model)
                        Destination.Learn -> InsightList("A little deeper", "Choose an idea. Your tutor explains it, then asks you to recall it in your own words.", state.library, "Choose something worth learning", "Save an idea first, then start a source-grounded conversation.", model, true)
                        Destination.Review -> ReviewScreen(state, model)
                        Destination.Explore -> ExploreScreen(state, model)
                    }
                }
            }
        }
    }
}
@Composable internal fun PageTitle(title: String, description: String) {
    Column(Modifier.padding(top = 22.dp, bottom = 20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(title, style = MaterialTheme.typography.headlineLarge, fontFamily = FontFamily.Serif, color = Ink)
        Text(description, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
@Composable private fun StatusMessage(text: String, error: Boolean, dismiss: () -> Unit) {
    Surface(color = if (error) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.secondaryContainer, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(Modifier.padding(start = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(text, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
            IconButton(onClick = dismiss) { Icon(Icons.Default.Close, "Dismiss message") }
        }
    }
}
@Composable private fun EmptyState(title: String, description: String) {
    Column(Modifier.fillMaxWidth().padding(vertical = 30.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(title, style = MaterialTheme.typography.titleLarge, fontFamily = FontFamily.Serif)
        Text(description, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
@Composable private fun Discover(state: ScreenState, input: String, change: (String) -> Unit, import: () -> Unit, writeNote: () -> Unit, model: LearningViewModel) {
    LazyColumn(verticalArrangement = Arrangement.spacedBy(14.dp), contentPadding = PaddingValues(bottom = 28.dp)) {
        item { PageTitle("Follow a good idea.", "Turn something you read into something you understand.") }
        item {
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(20.dp)) {
                Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("BRING YOUR CURIOSITY", style = MaterialTheme.typography.labelSmall, letterSpacing = 1.5.sp, fontWeight = FontWeight.Bold)
                    Text("Start with an article", style = MaterialTheme.typography.titleLarge, fontFamily = FontFamily.Serif)
                    OutlinedTextField(value = input, onValueChange = change, modifier = Modifier.fillMaxWidth(), label = { Text("Article URL") }, placeholder = { Text("https://…") }, maxLines = 3, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                    Button(onClick = import, enabled = input.isNotBlank() && !state.busy) { Icon(Icons.Default.Add, null); Spacer(Modifier.width(6.dp)); Text("Add to learning feed") }
                    Text("Or share a link from your browser. Imports continue in the background.", style = MaterialTheme.typography.bodySmall)
                    OutlinedButton(onClick = writeNote) { Icon(Icons.Default.EditNote, null); Spacer(Modifier.width(6.dp)); Text("Write or import a note") }
                }
            }
        }
        if (state.jobs.isNotEmpty()) item { ImportJobs(state.jobs, model) }
        item { Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("Today's ideas", style = MaterialTheme.typography.titleLarge, fontFamily = FontFamily.Serif, modifier = Modifier.weight(1f))
            TextButton(onClick = model::refresh, enabled = !state.busy) { Text("Refresh") }
        } }
        if (state.feed.isEmpty()) item { EmptyState("Room for your next discovery", "Add an article to build your feed. New ideas will appear here after your server finishes processing it.") }
        items(state.feed, key = { it.id }) { item -> InsightCard(item, model) }
    }
}
@Composable private fun InsightCard(item: Insight, model: LearningViewModel, startLearning: Boolean = false) {
    OutlinedCard(onClick = { if (startLearning) model.learn(item) else model.select(item) }, modifier = Modifier.fillMaxWidth(), colors = CardDefaults.outlinedCardColors(containerColor = Color.White), shape = RoundedCornerShape(18.dp)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(item.attribution(), style = MaterialTheme.typography.labelSmall, color = Clay)
            Text(item.title, style = MaterialTheme.typography.titleLarge, fontFamily = FontFamily.Serif)
            Text(item.idea, style = MaterialTheme.typography.bodyMedium, maxLines = 5, overflow = TextOverflow.Ellipsis)
            if (item.tags.isNotEmpty()) Text(item.tags.joinToString("  ·  "), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                if (startLearning) Text("Start learning →", color = Ink, style = MaterialTheme.typography.labelLarge)
                else {
                    TextButton(onClick = { model.learn(item) }) { Text("Learn this") }
                    TextButton(onClick = { model.save(item) }, enabled = !item.saved) { Text(if (item.saved) "Saved" else "Save") }
                    TextButton(onClick = { model.dismiss(item) }) { Text("Dismiss") }
                }
            }
        }
    }
}
@Composable private fun InsightList(title: String, description: String, items: List<Insight>, emptyTitle: String, emptyDescription: String, model: LearningViewModel, learn: Boolean = false) {
    LazyColumn(verticalArrangement = Arrangement.spacedBy(14.dp), contentPadding = PaddingValues(bottom = 28.dp)) {
        item { PageTitle(title, description) }
        item { TextButton(onClick = model::refresh) { Text("Refresh from server") } }
        if (items.isEmpty()) item { EmptyState(emptyTitle, emptyDescription) }
        items(items, key = { it.id }) { InsightCard(it, model, learn) }
    }
}
@Composable private fun SourceLink(url: String?, label: String = "Open original source") {
    val uri = LocalUriHandler.current
    if (url != null) TextButton(onClick = { runCatching { uri.openUri(url) } }, contentPadding = PaddingValues(0.dp)) {
        Icon(Icons.Default.OpenInNew, null, Modifier.size(16.dp)); Spacer(Modifier.width(8.dp)); Text(label)
    }
}
@Composable private fun InsightDetail(item: Insight, model: LearningViewModel) {
    LazyColumn(verticalArrangement = Arrangement.spacedBy(18.dp), contentPadding = PaddingValues(bottom = 32.dp)) {
        item { PageTitle(item.title, item.attribution()) }
        item { Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(onClick = { model.learn(item) }) { Text("Learn this idea") }
            OutlinedButton(onClick = { model.save(item) }, enabled = !item.saved) { Text(if (item.saved) "Saved" else "Save") }
        } }
        if (item.isNote()) item { ForgetNote(item, model) }
        item { Text("AI interpretation", style = MaterialTheme.typography.labelMedium, color = Clay) }
        item { SelectionContainer { Text(item.idea, style = MaterialTheme.typography.titleLarge, fontFamily = FontFamily.Serif) } }
        item { SelectionContainer { Text(item.explanation, style = MaterialTheme.typography.bodyLarge) } }
        if (item.whyItMatters.isNotBlank()) item { Text("Why it matters", style = MaterialTheme.typography.titleMedium); Spacer(Modifier.height(8.dp)); Text(item.whyItMatters) }
        if (item.evidence.isNotBlank()) item { Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(16.dp)) {
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (item.isNote()) "FROM YOUR NOTE" else "FROM THE SOURCE", style = MaterialTheme.typography.labelSmall)
                SourceCoverage(item)
                SelectionContainer { Text(item.evidence, fontFamily = FontFamily.Serif, style = MaterialTheme.typography.bodyLarge) }
                SourceLink(item.linkUrl())
            }
        } }
        item { SourceLink(item.linkUrl(), item.sourceTitle.ifBlank { "Open source article" }) }
    }
}
@Composable private fun SourceCoverage(item: Insight) {
    Text("Source: ${item.coverage.ifBlank { "coverage unavailable" }}", style = MaterialTheme.typography.labelMedium)
    if (item.sourceTruncated) Text("The source was shortened before AI processing. Check the original article for full context.", style = MaterialTheme.typography.bodySmall)
}
@Composable private fun Tutor(state: ScreenState, model: LearningViewModel) {
    var message by rememberSaveable(key = tutorDraftKey(state.scope, state.selected?.id.orEmpty())) { mutableStateOf("") }
    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(14.dp), contentPadding = PaddingValues(bottom = 16.dp)) {
            item { PageTitle("Make it yours.", state.selected?.title.orEmpty()) }
            state.selected?.let { insight -> item { SourceCoverage(insight) } }
            if (state.chat.isEmpty() && !state.chatBusy) item { Button(onClick = { model.chat("") }) { Text("Start explanation") } }
            items(state.chat) { line ->
                Surface(color = if (line.role == "You") MaterialTheme.colorScheme.surfaceVariant else Color.White, shape = RoundedCornerShape(18.dp)) {
                    Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text(if (line.role == "You") "YOU" else "AI INTERPRETATION", style = MaterialTheme.typography.labelSmall, color = Clay)
                        SelectionContainer { Text(line.text, style = MaterialTheme.typography.bodyLarge) }
                        if (line.question.isNotBlank()) Text(line.question, style = MaterialTheme.typography.titleMedium, color = Ink)
                        line.citations.forEach { citation ->
                            HorizontalDivider()
                            val cachedTitle = (state.feed + state.library).firstOrNull { it.documentId == citation.documentId }?.sourceTitle
                            Text(citationTitle(citation, state.selected, cachedTitle), style = MaterialTheme.typography.labelSmall, color = Clay)
                            Text(citation.passage, style = MaterialTheme.typography.bodySmall)
                            SourceLink(citation.linkUrl(), "Check source")
                        }
                    }
                }
            }
        }
        Row(Modifier.fillMaxWidth().imePadding().padding(vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(message, { message = it }, Modifier.weight(1f), label = { Text("Explain it in your own words") }, maxLines = 4, enabled = !state.chatBusy)
            Button(onClick = { model.chat(message); message = "" }, enabled = message.isNotBlank() && !state.chatBusy) { Text("Send") }
        }
    }
}
@Composable private fun ReviewScreen(state: ScreenState, model: LearningViewModel) {
    LazyColumn(verticalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(bottom = 32.dp)) {
        item { PageTitle("Bring it back.", "A moment of recall makes a useful idea easier to find next time.") }
        item { TextButton(onClick = model::loadReviews, enabled = !state.busy) { Text("Refresh due reviews") } }
        val review = state.reviews.firstOrNull()
        if (review == null) item { EmptyState(if (state.reviewsLoaded) "You're up to date" else "Your review queue", if (state.reviewsLoaded) "No reviews are due. Learn an idea with your tutor to begin a recall schedule." else "Connect to your server to check which ideas are due for recall.") }
        else {
            item { Text("${state.reviews.size} due", style = MaterialTheme.typography.labelLarge, color = Clay) }
            item { Text(review.insightTitle, style = MaterialTheme.typography.titleLarge, fontFamily = FontFamily.Serif) }
            item { Text(review.question, style = MaterialTheme.typography.headlineSmall) }
            if (!state.revealed) item {
                Text("Try to answer from memory before checking the source.")
                Spacer(Modifier.height(16.dp))
                Button(onClick = { model.revealReview(review) }, enabled = !state.busy) { Text("Show explanation") }
            } else {
                state.reviewSource?.let { source -> item {
                    SelectionContainer { Text(source.explanation, style = MaterialTheme.typography.bodyLarge) }
                    Spacer(Modifier.height(12.dp)); Text(source.evidence, style = MaterialTheme.typography.bodyMedium); SourceLink(source.linkUrl())
                } }
                item { Text("How well did you remember?", style = MaterialTheme.typography.titleMedium) }
                items(listOf("forgotten" to "Forgotten", "difficult" to "With effort", "remembered" to "Remembered", "easy" to "Easily")) { (rating, label) ->
                    OutlinedButton(onClick = { model.rate(review, rating) }, enabled = !state.busy, modifier = Modifier.fillMaxWidth()) { Text(label) }
                }
            }
        }
    }
}
@Composable private fun ExploreScreen(state: ScreenState, model: LearningViewModel) {
    var query by rememberSaveable { mutableStateOf("") }
    LazyColumn(verticalArrangement = Arrangement.spacedBy(14.dp), contentPadding = PaddingValues(bottom = 32.dp)) {
        item { PageTitle("Connect the dots.", "Search the ideas and sources in your knowledge library.") }
        item { OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth(), label = { Text("Search ideas") }, singleLine = true) }
        item { Button(onClick = { model.search(query) }, enabled = query.isNotBlank() && !state.busy) { Text("Search") } }
        if (state.results.isEmpty()) item { EmptyState(if (state.searched) "No matching ideas" else "Where will curiosity lead?", if (state.searched) "Try a different topic or add more sources to your library." else "Search by a topic, phrase, or question. Results come from your server.") }
        items(state.results, key = { it.id }) { InsightCard(it, model) }
    }
}
@OptIn(ExperimentalComposeUiApi::class)
@Composable private fun ConnectionDialog(connection: Connection?, model: LearningViewModel, onDismiss: () -> Unit) {
    var server by remember(connection?.scope) { mutableStateOf(connection?.server.orEmpty()) }
    var token by remember(connection?.scope) { mutableStateOf(connection?.token.orEmpty()) }
    var http by remember(connection?.scope) { mutableStateOf(connection?.server?.startsWith("http://") == true) }
    var error by remember { mutableStateOf<String?>(null) }
    ExcludeFromAutofill()
    AlertDialog(onDismissRequest = { if (connection != null) onDismiss() }, title = { Text("Connect your server", fontFamily = FontFamily.Serif) },
        text = { InterceptPlatformTextInput(NoPersonalizedLearning) { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Use your self-hosted Open Learning server and its access token. Your saved library stays on this device.")
            OutlinedTextField(server, { server = it }, Modifier.fillMaxWidth(), label = { Text("Server URL") }, placeholder = { Text("https://learning.example.com") }, singleLine = true, keyboardOptions = TextInputPrivacy.credentialKeyboard(KeyboardType.Uri))
            OutlinedTextField(token, { token = it }, Modifier.fillMaxWidth(), label = { Text("Access token") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, keyboardOptions = TextInputPrivacy.credentialKeyboard(KeyboardType.Password))
            Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(http, { http = it }); Text("Allow HTTP for a trusted local server", style = MaterialTheme.typography.bodySmall) }
            if (http) Text("HTTP sends your token and learning content without encryption. Use only on a network you trust.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            Text("Tokens are encrypted with Android Keystore. Changing server or account switches to a separate cache.", style = MaterialTheme.typography.bodySmall)
        } } },
        confirmButton = { TextButton(onClick = { error = model.configure(server, token, http); if (error == null) onDismiss() }, enabled = server.isNotBlank() && token.isNotBlank()) { Text("Connect") } },
        dismissButton = { if (connection != null) TextButton(onClick = onDismiss) { Text("Cancel") } })
}
