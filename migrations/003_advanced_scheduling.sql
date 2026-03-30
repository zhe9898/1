"""
Database migration for advanced scheduling features

Revision ID: 003_advanced_scheduling
Revises: 002_retry_delay
Create Date: 2026-03-29
"""

# ============================================================================
# Job 表新增字段
# ============================================================================

-- 边缘算力字段
ALTER TABLE jobs ADD COLUMN data_locality_key VARCHAR(255);
CREATE INDEX idx_jobs_data_locality_key ON jobs(data_locality_key);

ALTER TABLE jobs ADD COLUMN max_network_latency_ms INTEGER;
ALTER TABLE jobs ADD COLUMN prefer_cached_data INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN power_budget_watts INTEGER;
ALTER TABLE jobs ADD COLUMN thermal_sensitivity VARCHAR(32);
ALTER TABLE jobs ADD COLUMN cloud_fallback_enabled INTEGER DEFAULT 0;

-- 调度策略字段
ALTER TABLE jobs ADD COLUMN scheduling_strategy VARCHAR(32);
CREATE INDEX idx_jobs_scheduling_strategy ON jobs(scheduling_strategy);

ALTER TABLE jobs ADD COLUMN affinity_labels JSON DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN affinity_rule VARCHAR(32);
ALTER TABLE jobs ADD COLUMN anti_affinity_key VARCHAR(128);
CREATE INDEX idx_jobs_anti_affinity_key ON jobs(anti_affinity_key);

-- 业务调度字段
ALTER TABLE jobs ADD COLUMN parent_job_id VARCHAR(128);
CREATE INDEX idx_jobs_parent_job_id ON jobs(parent_job_id);

ALTER TABLE jobs ADD COLUMN depends_on JSON DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN gang_id VARCHAR(128);
CREATE INDEX idx_jobs_gang_id ON jobs(gang_id);

ALTER TABLE jobs ADD COLUMN batch_key VARCHAR(128);
CREATE INDEX idx_jobs_batch_key ON jobs(batch_key);

ALTER TABLE jobs ADD COLUMN preemptible INTEGER DEFAULT 1;
ALTER TABLE jobs ADD COLUMN deadline_at TIMESTAMP;
CREATE INDEX idx_jobs_deadline_at ON jobs(deadline_at);

ALTER TABLE jobs ADD COLUMN sla_seconds INTEGER;

-- 重试延迟字段（P1已完成）
ALTER TABLE jobs ADD COLUMN retry_at TIMESTAMP;
CREATE INDEX idx_jobs_retry_at ON jobs(retry_at);

-- ============================================================================
-- Node 表新增字段
-- ============================================================================

-- Kind 维度（正式合同）
ALTER TABLE nodes ADD COLUMN accepted_kinds JSON DEFAULT '[]';

-- 边缘算力字段
ALTER TABLE nodes ADD COLUMN network_latency_ms INTEGER;
ALTER TABLE nodes ADD COLUMN bandwidth_mbps INTEGER;
ALTER TABLE nodes ADD COLUMN cached_data_keys JSON DEFAULT '[]';
ALTER TABLE nodes ADD COLUMN power_capacity_watts INTEGER;
ALTER TABLE nodes ADD COLUMN current_power_watts INTEGER;
ALTER TABLE nodes ADD COLUMN thermal_state VARCHAR(32);
ALTER TABLE nodes ADD COLUMN cloud_connectivity VARCHAR(32);
