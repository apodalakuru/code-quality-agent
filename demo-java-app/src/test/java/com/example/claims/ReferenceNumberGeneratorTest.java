package com.example.claims;

import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class ReferenceNumberGeneratorTest {

    @Test
    void generatesReferenceInExpectedFormat() {
        String ref = new ReferenceNumberGenerator().next();
        assertTrue(ref.matches("CLM\\d{9}"), "unexpected format: " + ref);
    }
}
