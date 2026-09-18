# Create a read-only database user

Run on your PostgreSQL database (replace the password):

```sql
CREATE ROLE agentdesk_ro LOGIN PASSWORD '<strong-password>';
GRANT CONNECT ON DATABASE your_db TO agentdesk_ro;
GRANT USAGE ON SCHEMA public TO agentdesk_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentdesk_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agentdesk_ro;
ALTER ROLE agentdesk_ro SET default_transaction_read_only = on;
ALTER ROLE agentdesk_ro SET statement_timeout = '15s';
```

Allow AgentDesk's egress IP in your firewall (shown on the dataset page once hosting is fixed). Use SSL mode `require` or stricter.
