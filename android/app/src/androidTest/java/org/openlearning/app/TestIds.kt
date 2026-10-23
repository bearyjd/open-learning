package org.openlearning.app

/** Document ids shaped like the server's uuid4 strings: production refuses anything outside [0-9a-fA-F-] for a forget. */
internal object TestIds {
    const val DOC_A = "aaaaaaaa-0000-4000-8000-00000000000a"
    const val DOC_B = "bbbbbbbb-0000-4000-8000-00000000000b"
    const val DOC_OTHER = "cccccccc-0000-4000-8000-00000000000c"
    const val NOTE_DOC = "dddddddd-0000-4000-8000-00000000000d"
}
