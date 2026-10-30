CREATE TABLE kr_portals.stepstone_candidates (
 external_id text PRIMARY KEY CHECK (external_id ~ '^[0-9]+$'),
 source_url text NOT NULL CHECK (source_url LIKE 'https://www.stepstone.de/stellenangebote--%'),
 row_data jsonb NOT NULL CHECK (jsonb_typeof(row_data)='object'),
 search_keys text[] NOT NULL DEFAULT '{}',
 first_seen timestamptz NOT NULL DEFAULT now(),
 last_seen timestamptz NOT NULL DEFAULT now(),
 last_run_id uuid NOT NULL REFERENCES kr_portals.runs(id),
 detail_status text NOT NULL DEFAULT 'pending',
 detail_checked_at timestamptz
);
ALTER TABLE kr_portals.stepstone_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON kr_portals.stepstone_candidates FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON kr_portals.stepstone_candidates TO stepstone_archive_writer;
CREATE POLICY stepstone_candidate_writer ON kr_portals.stepstone_candidates TO stepstone_archive_writer USING(true) WITH CHECK(true);
CREATE INDEX stepstone_candidates_search_keys ON kr_portals.stepstone_candidates USING gin(search_keys);
CREATE INDEX stepstone_candidates_pending ON kr_portals.stepstone_candidates(detail_status,last_seen);
