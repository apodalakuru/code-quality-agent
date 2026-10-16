package com.example.claims;

import java.math.BigDecimal;

/** Applies a patient-pay amount to a deductible. Contains a deliberately seeded empty-catch finding. */
public class AccumulatorCalculator {

    /**
     * Returns how much of {@code patientPay} counts toward the deductible,
     * given the deductible limit and what has already been applied.
     */
    public BigDecimal amountTowardDeductible(String patientPay, BigDecimal limit, BigDecimal applied) {
        BigDecimal pay = BigDecimal.ZERO;
        try {
            pay = new BigDecimal(patientPay);
        } catch (NumberFormatException e) {
            // FINDING: exception silently swallowed (Sonar java:S108 / S1166)
        }
        BigDecimal remaining = limit.subtract(applied).max(BigDecimal.ZERO);
        return pay.min(remaining);
    }
}
