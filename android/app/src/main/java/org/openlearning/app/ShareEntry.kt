package org.openlearning.app

/**
 * Whether an intent's share should reach the note editor. A recreated activity (rotation, fold, process-death relaunch)
 * or a relaunch from recents gets the old SEND intent again, which must never replay a share that was already handled.
 * [isShare] is false once the share was consumed and the intent replaced.
 */
internal fun shouldHandleShare(isShare: Boolean, isNewIntent: Boolean, recreated: Boolean, fromHistory: Boolean) =
    isShare && (isNewIntent || (!recreated && !fromHistory))

/**
 * A shared note needs the connected account's draft, so it waits until the view model has switched to that account
 * and finished loading that account's saved draft. Both kinds also wait for the limits fetch to finish (loaded or failed):
 * the share cap depends on the server's limit, so a route judged against the defaults could wrongly reject a valid share.
 * A rejected share does not need the draft.
 */
internal fun shareDeferred(route: ShareRoute, accountReady: Boolean, draftReady: Boolean, limitsLoaded: Boolean) = when (route) {
    is ShareRoute.Note -> !(accountReady && draftReady && limitsLoaded)
    is ShareRoute.TooLong -> !(accountReady && limitsLoaded)
    else -> false
}

/** Only a link goes to the import field; shared text goes to the note editor instead. */
internal fun importTextFor(route: ShareRoute): String? = (route as? ShareRoute.Url)?.url

/** An identical share to one already waiting (or already in the draft) is dropped, so a repeat never re-asks. */
internal fun acceptShare(incoming: String?, vararg held: String?): String? = incoming?.takeUnless { it in held }

internal fun replaceDialogText(fromShare: Boolean) =
    "The ${if (fromShare) "shared text" else "file"} will replace the text you typed. This can't be undone."

internal fun shareTooLongMessage(route: ShareRoute.TooLong) =
    "That shared text is too long to add as a note (${formatCount(route.chars)} characters; the most accepted is ${formatCount(route.cap)}). Nothing was added."

/**
 * Runs the draft load and marks the draft ready even if the load throws, so a failing DAO call cannot block shares for
 * good. A load for an account that is no longer current never marks ready. The exception still propagates.
 */
internal suspend fun loadDraftThen(isCurrent: () -> Boolean, markReady: () -> Unit, load: suspend () -> Unit) {
    try { load() } finally { if (isCurrent()) markReady() }
}
