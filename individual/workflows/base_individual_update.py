import logging

from core.models import User
from individual.workflows.utils import DataUpdateWorkflow
from individual.services import IndividualImportService

logger = logging.getLogger(__name__)


def process_update_individuals_workflow(user_uuid, upload_uuid):
    # Call the records validation service directly with the provided arguments
    user = User.objects.get(id=user_uuid)
    service = DataUpdateWorkflow(upload_uuid, user_uuid)
    service.validate_dataframe_headers(True)
    service.execute(update_sql)
    IndividualImportService(user).synchronize_data_for_reporting(upload_uuid)


update_sql = """
CREATE OR REPLACE FUNCTION filter_jsonb(data jsonb, schema jsonb)
RETURNS jsonb AS $$
DECLARE
  key text;
  value text;
  result jsonb := '{}';
BEGIN
  FOR key, value IN SELECT * FROM jsonb_each_text(data)
  LOOP
    IF schema ? key THEN
      result := result || jsonb_build_object(key, value);
    END IF;
  END LOOP;
  RETURN result;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
            CREATE TYPE failing_entry_individual_upload AS (
            uuids TEXT[],
            ordinals INT[]
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;

DO $$
declare
    current_upload_id UUID := %s::UUID;
    userUUID UUID := %s::UUID;
    failing_entries UUID[];
    json_schema jsonb;

    failing_entries_invalid_id failing_entry_individual_upload;
BEGIN
    -- existing code for finding failing_entries_first_name, failing_entries_last_name, failing_entries_dob
    -- Check if any entries have invalid Json_ext according to the schema
    SELECT ARRAY_AGG("UUID") AS "UUID", ARRAY_AGG("ordinal") AS "ORDINALS" INTO failing_entries_invalid_id
    FROM (
        SELECT ("Json_ext" ->> 'ID')::UUID as individual_uuid,  row_number() OVER (ORDER BY "UUID") AS ordinal, "UUID"
        FROM individual_individualdatasource
        WHERE upload_id = current_upload_id
    ) AS f
    WHERE not individual_uuid in (select "UUID" from individual_individual ii);

    IF failing_entries_invalid_id IS NOT NULL THEN
        UPDATE individual_individualdatasourceupload
        SET error = coalesce(error, '{}'::jsonb) || jsonb_build_object('errors', jsonb_build_object(
                            'error', 'Invalid entries',
                            'timestamp', NOW()::text,
                            'upload_id', current_upload_id::text,
                            'failing_entries_invalid_id', failing_entries_invalid_id
                        ))
        WHERE "UUID" = current_upload_id;

       update individual_individualdatasourceupload set status='FAIL' where "UUID" = current_upload_id;
    -- If no invalid entries, then proceed with the data manipulation
    ELSE
        begin
          with updated_individuals as ( UPDATE individual_individual
            SET first_name = COALESCE(f."Json_ext"->>'first_name', first_name),
            last_name = COALESCE(f."Json_ext"->>'last_name', last_name),
            dob = COALESCE(to_date(NULLIF(TRIM(f."Json_ext"->>'dob'), ''), 'YYYY-MM-DD'), dob),
            location_id = loc."LocationId",
            "DateUpdated" = NOW(),
            "Json_ext" = f."Json_ext"
            FROM individual_individualdatasource f
            LEFT JOIN "tblLocations" AS loc
                    ON loc."LocationName" = f."Json_ext"->>'location_name'
                    AND loc."LocationCode" = f."Json_ext"->>'location_code'
                    AND loc."LocationType"='V'
                    AND loc."ValidityTo" IS NULL
            WHERE individual_individual."UUID" = (f."Json_ext" ->> 'ID')::UUID
            returning individual_individual."UUID", f."UUID" as "individualdatasource_id")

            UPDATE individual_individualdatasource
      SET individual_id = u."UUID"
      FROM updated_individuals u
      WHERE upload_id=current_upload_id
        and individual_individualdatasource.individual_id is null
        and "isDeleted"=False
        and individual_individualdatasource."UUID" = u.individualdatasource_id;


            update individual_individualdatasourceupload set status='PARTIAL_SUCCESS', error='{}' where "UUID" = current_upload_id;
            EXCEPTION
              WHEN OTHERS then

              update individual_individualdatasourceupload set status='FAIL' where "UUID" = current_upload_id;
                  UPDATE individual_individualdatasourceupload
                  SET error = coalesce(error, '{}'::jsonb) || jsonb_build_object('errors', jsonb_build_object(
                                      'error', SQLERRM,
                                      'timestamp', NOW()::text,
                                      'upload_id', current_upload_id::text
                                  ))
                  WHERE "UUID" = current_upload_id;
                END;
        END IF;
        end $$
        """
