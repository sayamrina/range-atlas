-- Metadata layer of Range Atlas. Occurrences are loaded from the lakehouse; model runs are written by the API.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS species (
    slug        text PRIMARY KEY,
    idx         integer NOT NULL UNIQUE,
    name        text NOT NULL,
    common      text,
    family      text,
    source      text NOT NULL CHECK (source IN ('gbif', 'obis')),
    source_key  integer NOT NULL,
    modelled    boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS occurrences (
    occurrence_id  text PRIMARY KEY,
    species        text NOT NULL REFERENCES species (slug),
    basis          text,
    year           integer,
    country        text,
    uncertainty_m  double precision,
    dataset_key    text,
    license        text,
    geom           geometry (Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS occurrences_geom ON occurrences USING gist (geom);
CREATE INDEX IF NOT EXISTS occurrences_species ON occurrences (species);

CREATE TABLE IF NOT EXISTS runs (
    id          bigserial PRIMARY KEY,
    species     text NOT NULL REFERENCES species (slug),
    backend     text NOT NULL,                      -- 'local' or 'aws-batch'
    external_id text,                               -- AWS Batch job id
    status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    created     timestamptz NOT NULL DEFAULT now(),
    started     timestamptz,
    finished    timestamptz,
    result      jsonb,
    error       text
);
CREATE INDEX IF NOT EXISTS runs_species ON runs (species, created DESC);
