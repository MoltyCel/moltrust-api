-- Content-Scout review queue. v0 = draft-and-queue only; nothing here publishes.
-- Rows are drafts a human reviews in the Console CLI before any manual publish.
CREATE TABLE IF NOT EXISTS content_review_queue (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL CHECK (source IN ('discovery', 'newsscout')),
    source_ref      TEXT NOT NULL UNIQUE,           -- issue URL / article URL (dedupe key)
    classification  TEXT NOT NULL CHECK (classification IN ('pass', 'watch', 'drop')),
    class_reason    TEXT,
    draft_type      TEXT NOT NULL DEFAULT 'none' CHECK (draft_type IN ('gh_comment', 'blog_post', 'none')),
    target          TEXT,                            -- repo#issue | blog-slug
    draft_md        TEXT,
    verify_status   JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_used      TEXT,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_est        NUMERIC(10, 5) NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'pending_review'
                    CHECK (state IN ('pending_review', 'approved', 'discarded', 'published')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_crq_state       ON content_review_queue (state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_crq_source_ref  ON content_review_queue (source_ref);
