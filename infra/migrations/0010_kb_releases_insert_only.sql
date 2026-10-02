-- P7 B5: a KB release is immutable the moment it is published. Every case is
-- stamped with the release id that produced it (cases.kb_release_id), so a
-- rewritten release would silently change what a stored case claims to have
-- been argued under. Inserts only; corrections are a NEW release id.

CREATE OR REPLACE FUNCTION kb_releases_insert_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'kb_releases is insert-only: a published release is immutable - publish a new release id';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kb_releases_insert_only ON kb_releases;
CREATE TRIGGER kb_releases_insert_only BEFORE UPDATE OR DELETE ON kb_releases
  FOR EACH ROW EXECUTE FUNCTION kb_releases_insert_only();
