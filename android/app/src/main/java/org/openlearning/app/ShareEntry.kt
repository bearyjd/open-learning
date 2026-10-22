package org.openlearning.app

/** A shared note needs the connected account's draft, so it waits until the view model has switched to that account. */
internal fun shareDeferred(route: ShareRoute, accountReady: Boolean) = route is ShareRoute.Note && !accountReady

/** Only a link goes to the import field; shared text goes to the note editor instead. */
internal fun importTextFor(route: ShareRoute): String? = (route as? ShareRoute.Url)?.url
