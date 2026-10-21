package com.example.claims;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

/** Looks up claim statuses for a member. Contains a deliberately seeded SQL injection finding. */
public class ClaimLookupDao {

    private final Connection connection;

    public ClaimLookupDao(Connection connection) {
        this.connection = connection;
    }

    public List<String> findClaimStatuses(String memberId) throws SQLException {
        // FINDING: user input concatenated into SQL (CodeQL java/sql-injection, Sonar S3649)
        String sql = "SELECT status FROM claim WHERE member_id = '" + memberId + "'";
        List<String> statuses = new ArrayList<>();
        try (Statement st = connection.createStatement();
             ResultSet rs = st.executeQuery(sql)) {
            while (rs.next()) {
                statuses.add(rs.getString("status"));
            }
        }
        return statuses;
    }
}
