import logging

from core.models import User
from individual.workflows.utils import SqlProcedurePythonWorkflow
from individual.services import IndividualImportService

logger = logging.getLogger(__name__)


def process_import_valid_individuals_workflow(user_uuid, upload_uuid, accepted=None):
    user = User.objects.get(id=user_uuid)
    service = SqlProcedurePythonWorkflow(upload_uuid, user_uuid, accepted)
    service.validate_dataframe_headers()
    if isinstance(accepted, list):
        service.execute(upload_sql_partial, [upload_uuid, user_uuid, accepted])
    else:
        service.execute(upload_sql, [upload_uuid, user_uuid])
    IndividualImportService(user).synchronize_data_for_reporting(upload_uuid)


upload_sql = """
DO $$
DECLARE
    current_upload_id UUID := %s::UUID;
    userUUID UUID := %s::UUID;
    failing_entries UUID[];
    json_schema jsonb;
    failing_entries_invalid_json UUID[];
    failing_entries_first_name UUID[];
    failing_entries_last_name UUID[];
    total_entries INT;
    total_valid_entries INT;
BEGIN
    -- Check if all required fields are present in the entries
    SELECT ARRAY_AGG("UUID") INTO failing_entries_first_name
    FROM individual_individualdatasource
    WHERE upload_id = current_upload_id AND individual_id IS NULL AND "isDeleted" = False AND NOT "Json_ext" ? 'first_name';
    SELECT ARRAY_AGG("UUID") INTO failing_entries_last_name
    FROM individual_individualdatasource
    WHERE upload_id = current_upload_id AND individual_id IS NULL AND "isDeleted" = False AND NOT "Json_ext" ? 'last_name';
    SELECT ARRAY_AGG("UUID") INTO failing_entries_invalid_json
    FROM individual_individualdatasource
    WHERE upload_id = current_upload_id AND individual_id IS NULL AND "isDeleted" = False AND NOT validate_json_schema(json_schema, "Json_ext");
    -- If any entries do not meet the criteria or missing required fields, set the error message in the upload table and do not proceed further
    IF failing_entries_invalid_json IS NOT NULL OR failing_entries_first_name IS NOT NULL OR failing_entries_last_name IS NOT NULL THEN
        UPDATE individual_individualdatasourceupload
        SET error = coalesce(error, '{}'::jsonb) || jsonb_build_object('errors', jsonb_build_object(
                            'error', 'Invalid entries',
                            'timestamp', NOW()::text,
                            'upload_id', current_upload_id::text,
                            'failing_entries_first_name', failing_entries_first_name,
                            'failing_entries_last_name', failing_entries_last_name,
                            'failing_entries_invalid_json', failing_entries_invalid_json
                        ))
        WHERE "UUID" = current_upload_id;

        UPDATE individual_individualdatasourceupload SET status = 'FAIL' WHERE "UUID" = current_upload_id;
    ELSE
        -- If no invalid entries, then proceed with the data manipulation
        WITH new_entry AS (
            INSERT INTO individual_individual(
                "UUID", "isDeleted", version, "UserCreatedUUID", "UserUpdatedUUID",
                "Json_ext", first_name, last_name, dob, location_id
            )
            SELECT gen_random_uuid(), false, 1, userUUID, userUUID,
                   "Json_ext",
                   "Json_ext"->>'first_name',
                   "Json_ext" ->> 'last_name',
                   to_date(NULLIF(TRIM("Json_ext" ->> 'dob'), ''), 'YYYY-MM-DD'),
                   loc."LocationId"
            FROM individual_individualdatasource AS ds
            LEFT JOIN "tblLocations" AS loc
                    ON loc."LocationName" = ds."Json_ext"->>'location_name'
                    AND loc."LocationCode" = ds."Json_ext"->>'location_code'
                    AND loc."LocationType"='V'
                    AND loc."ValidityTo" IS NULL
            WHERE ds.upload_id = current_upload_id
                AND ds.individual_id IS NULL
                AND ds."isDeleted" = False
                AND ds.validations ->> 'validation_errors' = '[]'
            RETURNING "UUID", "Json_ext"
        )
        UPDATE individual_individualdatasource
        SET individual_id = ne."UUID"
        FROM new_entry ne
        WHERE individual_individualdatasource.upload_id = current_upload_id
          AND individual_individualdatasource.individual_id IS NULL
          AND individual_individualdatasource."isDeleted" = False
          AND individual_individualdatasource."Json_ext" = ne."Json_ext"
          AND validations ->> 'validation_errors' = '[]';

        -- Calculate counts of valid and total entries
        SELECT count(*) INTO total_valid_entries
        FROM individual_individualdatasource
        WHERE upload_id = current_upload_id
          AND "isDeleted" = FALSE
          AND COALESCE(validations ->> 'validation_errors', '[]') = '[]';

        SELECT count(*) INTO total_entries
        FROM individual_individualdatasource
        WHERE upload_id = current_upload_id
          AND "isDeleted" = FALSE;

        -- Change status to SUCCESS if no invalid items, change to PARTIAL_SUCCESS otherwise
            UPDATE individual_individualdatasourceupload
            SET
                status = CASE
                    WHEN total_valid_entries = total_entries THEN 'SUCCESS'
                    ELSE 'PARTIAL_SUCCESS'
                END,
                error = CASE
                    WHEN total_valid_entries < total_entries THEN jsonb_build_object(
                        'error', 'Partial success due to some invalid entries',
                        'timestamp', NOW()::text,
                        'upload_id', current_upload_id::text,
                        'total_valid_entries', total_valid_entries,
                        'total_entries', total_entries
                    )
                    ELSE '{}'
                END
            WHERE "UUID" = current_upload_id;
    END IF;
EXCEPTION WHEN OTHERS THEN
    UPDATE individual_individualdatasourceupload SET status = 'FAIL', error = jsonb_build_object(
        'error', SQLERRM,
        'timestamp', NOW()::text,
        'upload_id', current_upload_id::text
    )
    WHERE "UUID" = current_upload_id;
END $$;
"""

upload_sql_partial = """
DO $$
DECLARE
    current_upload_id UUID := %s::UUID;
    userUUID UUID := %s::UUID;
    accepted UUID[] := %s::UUID[]; -- Placeholder for the accepted UUIDs array, can be NULL
    failing_entries UUID[];
    failing_entries_first_name UUID[];
    failing_entries_last_name UUID[];
    new_entry_results UUID[];
    new_entry_result UUID;
BEGIN
    -- Check if all required fields are present in the entries, with accepted filter applied if not NULL
    SELECT ARRAY_AGG("UUID") INTO failing_entries_first_name
    FROM individual_individualdatasource
    WHERE upload_id = current_upload_id AND individual_id IS NULL
    AND "isDeleted" = False AND NOT "Json_ext" ? 'first_name'
    AND (accepted IS NULL OR "UUID" = ANY(accepted));

    SELECT ARRAY_AGG("UUID") INTO failing_entries_last_name
    FROM individual_individualdatasource
    WHERE upload_id = current_upload_id AND individual_id IS NULL
    AND "isDeleted" = False AND NOT "Json_ext" ? 'last_name'
    AND (accepted IS NULL OR "UUID" = ANY(accepted));

    -- If any entries do not meet the criteria or missing required fields,
    -- set the error message in the upload table and do not proceed further
    IF failing_entries_first_name IS NOT NULL OR failing_entries_last_name IS NOT NULL THEN
        UPDATE individual_individualdatasourceupload
        SET error = coalesce(error, '{}'::jsonb) || jsonb_build_object('errors', jsonb_build_object(
                            'error', 'Invalid entries',
                            'timestamp', NOW()::text,
                            'upload_id', current_upload_id::text,
                            'failing_entries_first_name', failing_entries_first_name,
                            'failing_entries_last_name', failing_entries_last_name
                        ))
        WHERE "UUID" = current_upload_id;

        UPDATE individual_individualdatasourceupload SET status = 'FAIL' WHERE "UUID" = current_upload_id;
    ELSE
        -- If no invalid entries, then proceed with the data manipulation, considering the accepted filter
        WITH new_entry AS (
            INSERT INTO individual_individual(
                "UUID", "isDeleted", version, "UserCreatedUUID", "UserUpdatedUUID",
                "Json_ext", first_name, last_name, dob, location_id
            )
            SELECT gen_random_uuid(), false, 1, userUUID, userUUID,
                   "Json_ext",
                   "Json_ext"->>'first_name',
                   "Json_ext" ->> 'last_name',
                   to_date(NULLIF(TRIM("Json_ext" ->> 'dob'), ''), 'YYYY-MM-DD'),
                   loc."LocationId"
            FROM individual_individualdatasource AS ds
            LEFT JOIN "tblLocations" AS loc
                    ON loc."LocationName" = ds."Json_ext"->>'location_name'
                    AND loc."LocationCode" = ds."Json_ext"->>'location_code'
                    AND loc."LocationType"='V'
                    AND loc."ValidityTo" IS NULL
            WHERE ds.upload_id = current_upload_id
                AND ds.individual_id IS NULL
                AND ds."isDeleted" = False
                AND ds.validations ->> 'validation_errors' = '[]'
            AND (accepted IS NULL OR "UUID" = ANY(accepted))
            RETURNING "UUID", "Json_ext"
        )
        UPDATE individual_individualdatasource
        SET individual_id = ne."UUID"
        FROM new_entry ne
        WHERE individual_individualdatasource.upload_id = current_upload_id
          AND individual_individualdatasource.individual_id IS NULL
          AND individual_individualdatasource."isDeleted" = False
          AND individual_individualdatasource."Json_ext" = ne."Json_ext"
          AND validations ->> 'validation_errors' = '[]'
          AND (accepted IS NULL OR individual_individualdatasource."UUID" = ANY(accepted));
    END IF;
EXCEPTION WHEN OTHERS THEN
    UPDATE individual_individualdatasourceupload SET status = 'FAIL', error = jsonb_build_object(
        'error', SQLERRM,
        'timestamp', NOW()::text,
        'upload_id', current_upload_id::text
    )
    WHERE "UUID" = current_upload_id;
END $$;
"""
