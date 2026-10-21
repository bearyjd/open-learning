package org.openlearning.app

import com.google.gson.JsonParser
import org.junit.Assert.*
import org.junit.Test

class TextRequestJsonTest {
    private fun keys(request: TextRequest) = JsonParser.parseString(apiGson.toJson(request)).asJsonObject.keySet()

    @Test fun nullLabelIsOmitted() = assertEquals(setOf("title", "text"), keys(TextRequest("t", "x", null)))
    @Test fun labelIsIncluded() = assertEquals(setOf("title", "text", "label"), keys(TextRequest("t", "x", "l")))
}
