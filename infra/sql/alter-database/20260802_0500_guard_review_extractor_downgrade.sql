-- 滚动部署期间拒绝旧 Prompt 结果覆盖新版本复习资料，异常会使整次卡片写入事务回滚。
CREATE OR REPLACE FUNCTION learning_evidence.guard_learning_review_extractor_downgrade()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    current_version INTEGER;
    incoming_version INTEGER;
BEGIN
    current_version := COALESCE((regexp_match(OLD.extractor, 'review-card-v([0-9]+)'))[1]::INTEGER, 0);
    incoming_version := COALESCE((regexp_match(NEW.extractor, 'review-card-v([0-9]+)'))[1]::INTEGER, 0);
    IF current_version > incoming_version THEN
        RAISE EXCEPTION '拒绝旧版复习提炼器覆盖当前结果: current=%, incoming=%', OLD.extractor, NEW.extractor
            USING ERRCODE = '40001';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_learning_review_extractor_downgrade
    ON learning_evidence.learning_review_material;

CREATE TRIGGER trg_learning_review_extractor_downgrade
    BEFORE UPDATE ON learning_evidence.learning_review_material
    FOR EACH ROW
    EXECUTE FUNCTION learning_evidence.guard_learning_review_extractor_downgrade();
