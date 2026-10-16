package com.example.claims;

import java.util.Random;

/** Generates claim reference numbers. Contains a deliberately seeded weak-randomness finding. */
public class ReferenceNumberGenerator {

    // FINDING: java.util.Random is predictable (Sonar java:S2245)
    private final Random random = new Random();

    public String next() {
        long value = Math.abs(random.nextLong() % 1_000_000_000L);
        return String.format("CLM%09d", value);
    }
}
