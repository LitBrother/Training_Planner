import io
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from streamlit.testing.v1 import AppTest

from database.session import configure, session_scope
from models.orm import (
    AIAdjustment, AthleteProfile, RunningSession, Setting, StrengthLog,
    StrengthSetDetail, User,
)
from services import export_service as ex
from services import plan_service as ps
from services import strength_progress as sp

STRENGTH_PAGE = Path(__file__).resolve().parent.parent / "pages" / "strength.py"
LOG_DATE = date(2026, 10, 15)


def test_detailed_sets_round_trip_summary_and_legacy_remains_aggregate(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        log_id = sp.add_detailed_log(s, user, LOG_DATE, "bench press", [
            {"weight_kg": 90.1, "reps": 6},
            {"weight_kg": 100.0, "reps": 5},
            {"weight_kg": 100.0, "reps": 8},
        ])
        log = s.get(StrengthLog, log_id)
        details = sp.list_detailed_logs(s, user)
        assert [(item.set_number, item.weight_kg, item.reps) for item in details[0][1]] == [
            (1, 90.1, 6), (2, 100.0, 5), (3, 100.0, 8),
        ]
        assert (log.exercise, log.weight_kg, log.sets, log.reps) == (
            "Bench Press", 100.0, 3, 8
        )
        assert sp.est_1rm(log.weight_kg, log.reps) == 126.7

        frame = sp.log_frame(s, user)
        assert frame.iloc[0]["est_1rm"] == 126.7
        sp.update_detailed_log(s, user, log_id, date(2026, 10, 16), "bench press", [
            {"weight_kg": 80.4, "reps": 7},
            {"weight_kg": 95.2, "reps": 9},
        ])
        assert (log.date, log.weight_kg, log.sets, log.reps) == (
            date(2026, 10, 16), 95.2, 2, 9
        )
        assert [(item.set_number, item.weight_kg, item.reps)
                for item in sp.list_detailed_logs(s, user)[0][1]] == [
                    (1, 80.4, 7), (2, 95.2, 9)
                ]

        legacy = StrengthLog(
            user_id=user.id, date=LOG_DATE, exercise="Old Aggregate",
            weight_kg=50, sets=3, reps=8,
        )
        s.add(legacy)
        s.flush()
        with pytest.raises(ValueError, match="Detailed strength log not found"):
            sp.update_detailed_log(
                s, user, legacy.id, LOG_DATE, "Changed", [{"weight_kg": 10.0, "reps": 5}]
            )
        with pytest.raises(ValueError, match="per-set values"):
            sp.update_log(s, user, log_id, LOG_DATE, "Aggregate conversion", 100, 3, 8)
        assert [parent.id for parent, _ in sp.list_detailed_logs(s, user)] == [log_id]
        assert (legacy.weight_kg, legacy.sets, legacy.reps) == (50, 3, 8)
        assert s.scalar(select(StrengthSetDetail.id).where(
            StrengthSetDetail.strength_log_id == legacy.id
        )) is None


@pytest.mark.parametrize(
    ("set_details", "message"),
    [
        ([], "between 1 and 10"),
        ([{"weight_kg": -0.1, "reps": 5}], "Set 1: weight"),
        ([{"weight_kg": 500.1, "reps": 5}], "Set 1: weight"),
        ([{"weight_kg": 10.05, "reps": 5}], "0.1 kg increments"),
        ([{"weight_kg": float("nan"), "reps": 5}], "Set 1: weight"),
        ([{"weight_kg": 10.0, "reps": 0}], "Set 1: reps"),
        ([{"weight_kg": 10.0, "reps": 51}], "Set 1: reps"),
        ([{"weight_kg": 10.0, "reps": True}], "Set 1: reps"),
        ([{"weight_kg": 10.0, "reps": 5}] * 11, "between 1 and 10"),
    ],
)
def test_invalid_detailed_sets_do_not_mutate_existing_rows(db, set_details, message):
    with session_scope() as s:
        user = ps.ensure_user(s)
        log_id = sp.add_detailed_log(s, user, LOG_DATE, "Deadlift", [
            {"weight_kg": 80.0, "reps": 6},
            {"weight_kg": 82.5, "reps": 5},
        ])
        parent = s.get(StrengthLog, log_id)
        before_parent = (parent.date, parent.exercise, parent.weight_kg, parent.sets, parent.reps)
        before_rows = [(row.set_number, row.weight_kg, row.reps)
                       for row in sp.list_detailed_logs(s, user)[0][1]]

        with pytest.raises(ValueError, match=message):
            sp.update_detailed_log(s, user, log_id, date(2026, 10, 20), "Changed", set_details)
        assert (parent.date, parent.exercise, parent.weight_kg, parent.sets, parent.reps) == before_parent
        assert [(row.set_number, row.weight_kg, row.reps)
                for row in sp.list_detailed_logs(s, user)[0][1]] == before_rows
        with pytest.raises(ValueError, match=message):
            sp.add_detailed_log(s, user, LOG_DATE, "Invalid", set_details)
        assert len(sp.list_logs(s, user)) == 1


def test_detailed_operations_are_owner_scoped_and_delete_children_explicitly(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="detail-owner")
        other = ps.ensure_user(s, auth_uid="detail-other")
        own_id = sp.add_detailed_log(s, owner, LOG_DATE, "Press", [
            {"weight_kg": 40.0, "reps": 10}
        ])
        other_id = sp.add_detailed_log(s, other, LOG_DATE, "Squat", [
            {"weight_kg": 100.0, "reps": 5}
        ])
        assert [parent.id for parent, _ in sp.list_detailed_logs(s, owner)] == [own_id]

        with pytest.raises(ValueError, match="not found for this account"):
            sp.update_detailed_log(s, owner, other_id, LOG_DATE, "Fake", [
                {"weight_kg": 1.0, "reps": 1}
            ])
        with pytest.raises(ValueError, match="not found for this account"):
            sp.delete_log(s, owner, other_id)
        assert sp.list_detailed_logs(s, owner)[0][0].exercise == "Press"
        sp.delete_log(s, owner, own_id)
        assert s.scalar(select(StrengthSetDetail.id).where(
            StrengthSetDetail.strength_log_id == own_id
        )) is None
        assert s.scalar(select(StrengthSetDetail.id).where(
            StrengthSetDetail.strength_log_id == other_id
        )) is not None


def test_export_formats_are_scoped_allowlisted_and_manifest_matches(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="export-owner", email="private@example.test")
        other = ps.ensure_user(s, auth_uid="export-other", email="other@example.test")
        detailed_id = sp.add_detailed_log(s, owner, LOG_DATE, "Press", [
            {"weight_kg": 45.1, "reps": 8}
        ])
        sp.add_detailed_log(s, other, LOG_DATE, "Squat", [
            {"weight_kg": 120.0, "reps": 4}
        ])
        run = RunningSession(
            user_id=owner.id, date=LOG_DATE, weekday=3, slot="easy",
            name="Garmin import", run_type="Easy", variant="", planned_km=0,
            adjusted_km=0, actual_km=5.2, status="completed", source="garmin",
            garmin_id="garmin-activity-123",
        )
        other_run = RunningSession(
            user_id=other.id, date=LOG_DATE, weekday=3, slot="easy",
            name="Other import", run_type="Easy", variant="", planned_km=0,
            adjusted_km=0, actual_km=8.0, status="completed", source="garmin",
            garmin_id="other-activity",
        )
        s.add_all([run, other_run])
        s.add_all([
            Setting(user_id=owner.id, key="garmin_email_enc", value="ciphertext-private"),
            Setting(user_id=owner.id, key="garmin_password_enc", value="ciphertext-password"),
            Setting(user_id=owner.id, key="service_api_token", value="secret-token"),
            Setting(user_id=other.id, key="ordinary", value="other setting"),
            AthleteProfile(user_id=owner.id, training_focus="Strength", goal=""),
        ])
        s.add(AIAdjustment(
            user_id=owner.id, created_by_user_id=other.id,
            created_at=datetime(2026, 10, 15),
            session_date=LOG_DATE, kind="reduce_volume", old_km=5, new_km=4,
            reason="test", status="approved",
        ))
        s.flush()

        payload = json.loads(ex.to_json(s, owner))
        assert payload["version"] == 1
        assert [row["weight_kg"] for row in payload["tables"]["strength_set_details"]] == [45.1]
        assert payload["tables"]["strength_set_details"][0]["strength_log_id"] == detailed_id
        exported_run = next(
            row for row in payload["tables"]["running_sessions"]
            if row.get("name") == "Garmin import"
        )
        assert exported_run["garmin_id"] == "garmin-activity-123"
        assert exported_run["source"] == "garmin"
        serialized = json.dumps(payload)
        for forbidden in (
            "private@example.test", "export-owner", "ciphertext-private",
            "ciphertext-password", "secret-token", "other-activity",
        ):
            assert forbidden not in serialized
        assert "created_by_user_id" not in payload["tables"]["ai_adjustments"][0]
        assert "friendships" not in payload["tables"]

        csv_data = ex.dump_export(s, "csv", owner, "running_sessions")
        assert list(csv_data) == ["running_sessions"]
        assert "source" not in csv_data["running_sessions"][0]
        assert "garmin_id" not in csv_data["running_sessions"][0]
        exported_csv_run = next(
            row for row in csv_data["running_sessions"]
            if row.get("name") == "Garmin import"
        )
        assert exported_csv_run["actual_km"] == 5.2
        excel = pd.ExcelFile(io.BytesIO(ex.to_excel(s, owner)))
        assert "strength_set_details" in excel.sheet_names
        excel_runs = pd.read_excel(excel, sheet_name="running_sessions")
        assert "source" not in excel_runs.columns and "garmin_id" not in excel_runs.columns
        assert len(excel_runs[excel_runs["name"] == "Garmin import"]) == 1
        excel_users = pd.read_excel(excel, sheet_name="users")
        assert "auth_uid" not in excel_users.columns and "email" not in excel_users.columns
        excel_details = pd.read_excel(excel, sheet_name="strength_set_details")
        assert [(row.weight_kg, row.reps) for row in excel_details.itertuples()] == [(45.1, 8)]
        detail_csv = pd.read_csv(io.StringIO(ex.table_csv(s, "strength_set_details", owner)))
        assert [(row.weight_kg, row.reps) for row in detail_csv.itertuples()] == [(45.1, 8)]

        csv_manifest = ex.export_manifest("csv", owner, "strength_set_details")
        assert csv_manifest["tables"] == ["strength_set_details"]
        assert csv_manifest["scope"] == "Current signed-in account only; no other accounts."
        assert "Garmin source and activity identifiers" in csv_manifest["excluded"]
        assert "profile name" in csv_manifest["excluded"]
        assert "Saved Garmin credential settings are excluded and remain preserved by restore." in (
            csv_manifest["restore"]
        )
        json_manifest = ex.export_manifest("json", owner)
        assert set(json_manifest["tables"]) == set(ex.TABLES)
        assert "AIAdjustment records" in json_manifest["included"]
        assert "AI conversation/prompt history" in json_manifest["excluded"]
        assert "raw Garmin caches and wellness/recovery data" in json_manifest["excluded"]
        assert any("only approved plan preferences" in item for item in json_manifest["excluded"])
        assert "idempotent restore" in json_manifest["garmin_identifiers"]
        assert "JSON schema v1" in ex.manifest_text("json", owner)


def test_json_restore_updates_profile_name_without_changing_account_identity(db):
    with session_scope() as s:
        owner = ps.ensure_user(
            s, name="Archive Name", auth_uid="identity-safe-owner",
            email="identity-safe@example.test",
        )
        owner.name = "Archive Name"
        s.flush()
        backup = ex.to_json(s, owner)
        payload = json.loads(backup)
        assert payload["tables"]["users"][0]["name"] == "Archive Name"

        owner.name = "Current Name"
        s.flush()
        original_identity = (owner.id, owner.auth_uid, owner.email)
        counts = ex.restore_json(s, backup, owner)

        assert counts["users"] == 1
        assert owner.name == "Archive Name"
        assert (owner.id, owner.auth_uid, owner.email) == original_identity


def test_setting_exports_and_restore_use_safe_allowlist(db):
    sensitive = {
        "garmin_email_enc": "garmin-cipher",
        "garmin_password_enc": "garmin-password-cipher",
        "oauth_token": "oauth-token-value",
        "api_key": "api-key-value",
        "integration_secret": "integration-secret-value",
        "encryption_key": "encryption-key-value",
        "session_data": "session-cookie-value",
        "unknown_preference": "unknown-setting-value",
    }
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="settings-policy-owner")
        other = ps.ensure_user(s, auth_uid="settings-policy-other")
        s.add_all(
            Setting(user_id=owner.id, key=key, value=value)
            for key, value in sensitive.items()
        )
        s.add(Setting(user_id=other.id, key="goal", value="Other account preference"))
        ps.save_settings(s, owner, ps.get_settings(s, owner))
        owner_model_setting = s.scalars(select(Setting).where(
            Setting.user_id == owner.id, Setting.key == "openai_model"
        )).first()
        owner_model_setting.value = "secret-token-value"
        s.flush()

        json_backup = ex.to_json(s, owner)
        csv_settings = ex.table_csv(s, "settings", owner)
        excel_settings = pd.read_excel(
            io.BytesIO(ex.to_excel(s, owner)), sheet_name="settings"
        ).to_csv(index=False)
        for serialized in (json_backup, csv_settings, excel_settings):
            for forbidden in (
                *sensitive.keys(), *sensitive.values(), "secret-token-value",
                "Other account preference",
            ):
                assert forbidden not in serialized
        exported_settings = json.loads(json_backup)["tables"]["settings"]
        assert exported_settings
        assert all(row["key"] in ex.SAFE_SETTING_KEYS for row in exported_settings)

        payload = json.loads(json_backup)
        payload["tables"]["settings"].extend(
            {"key": key, "value": f"injected-{value}"}
            for key, value in sensitive.items()
        )
        payload["tables"]["settings"].append({
            "key": "openai_model", "value": "injected-secret-token-value"
        })
        counts = ex.restore_json(s, json.dumps(payload), owner)
        current_settings = {
            row.key: row.value for row in s.scalars(
                select(Setting).where(Setting.user_id == owner.id)
            )
        }
        other_settings = list(s.scalars(select(Setting).where(
            Setting.user_id == other.id
        )))
        assert counts["settings"] == len(exported_settings)
        assert current_settings["garmin_email_enc"] == "garmin-cipher"
        assert current_settings["garmin_password_enc"] == "garmin-password-cipher"
        assert current_settings["oauth_token"] == "oauth-token-value"
        assert current_settings["unknown_preference"] == "unknown-setting-value"
        assert current_settings["api_key"] == "api-key-value"
        assert current_settings["integration_secret"] == "integration-secret-value"
        assert current_settings["encryption_key"] == "encryption-key-value"
        assert current_settings["session_data"] == "session-cookie-value"
        assert current_settings["openai_model"] == "secret-token-value"
        assert [(row.key, row.value) for row in other_settings] == [
            ("goal", "Other account preference")
        ]


def test_export_and_restore_require_an_explicit_account_owner(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="missing-owner")
        other = ps.ensure_user(s, auth_uid="missing-other")
        ps.add_note(s, owner, LOG_DATE, "Training", "Owner note")
        ps.add_note(s, other, LOG_DATE, "Training", "Other note")
        backup = ex.to_json(s, owner)

        with pytest.raises(ValueError, match="authenticated account is required"):
            ex.to_json(s)
        with pytest.raises(ValueError, match="authenticated account is required"):
            ex.restore_json(s, backup)

        assert [note.text for note in ps.list_notes(s, owner)] == ["Owner note"]
        assert [note.text for note in ps.list_notes(s, other)] == ["Other note"]


def test_restore_legacy_and_current_detailed_backups_preserve_credentials_and_isolation(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="restore-owner")
        other = ps.ensure_user(s, auth_uid="restore-other")
        detailed_id = sp.add_detailed_log(s, owner, LOG_DATE, "Press", [
            {"weight_kg": 70.2, "reps": 8}, {"weight_kg": 75.0, "reps": 6}
        ])
        sp.add_detailed_log(s, other, LOG_DATE, "Other", [
            {"weight_kg": 110.0, "reps": 4}
        ])
        s.add_all([
            Setting(user_id=owner.id, key="garmin_email_enc", value="saved-email-cipher"),
            Setting(user_id=owner.id, key="garmin_password_enc", value="saved-password-cipher"),
        ])
        s.flush()
        current_backup = json.loads(ex.to_json(s, owner))
        current_backup["tables"]["strength_logs"][0]["id"] = 987654
        for row in current_backup["tables"]["strength_set_details"]:
            row["strength_log_id"] = 987654
        old_json = json.dumps(current_backup)
        legacy_backup = json.loads(old_json)
        legacy_backup["tables"].pop("strength_logs")
        legacy_backup["tables"].pop("strength_set_details")
        legacy_backup["tables"].pop("athlete_profiles")
        s.add(Setting(user_id=owner.id, key="goal_setting", value="temporary"))
        s.flush()

        current_counts = ex.restore_json(s, old_json, owner)
        restored = sp.list_detailed_logs(s, owner)
        assert current_counts["strength_set_details"] == 2
        assert restored[0][0].id != 987654
        assert restored[0][0].user_id == owner.id
        assert [(item.weight_kg, item.reps) for item in restored[0][1]] == [
            (70.2, 8), (75.0, 6)
        ]
        assert all(item.user_id == owner.id for item in restored[0][1])
        assert len(sp.list_detailed_logs(s, other)) == 1
        credential_values = {
            setting.key: setting.value for setting in s.scalars(select(Setting).where(
                Setting.user_id == owner.id
            ))
        }
        assert credential_values["garmin_email_enc"] == "saved-email-cipher"
        assert credential_values["garmin_password_enc"] == "saved-password-cipher"
        assert credential_values["goal_setting"] == "temporary"

        ex.restore_json(s, json.dumps(legacy_backup), owner)
        assert not sp.list_detailed_logs(s, owner)
        assert len(sp.list_logs(s, owner)) == 0
        assert len(sp.list_detailed_logs(s, other)) == 1
        assert s.scalar(select(Setting.value).where(
            Setting.user_id == owner.id, Setting.key == "garmin_password_enc"
        )) == "saved-password-cipher"


def test_restore_rejects_forged_cross_account_detail_parent_before_mutation(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="forged-owner")
        other = ps.ensure_user(s, auth_uid="forged-other")
        own_id = sp.add_detailed_log(s, owner, LOG_DATE, "Press", [
            {"weight_kg": 50.0, "reps": 8}
        ])
        other_id = sp.add_detailed_log(s, other, LOG_DATE, "Squat", [
            {"weight_kg": 100.0, "reps": 5}
        ])
        forged = json.loads(ex.to_json(s, owner))
        forged["tables"]["strength_set_details"][0]["strength_log_id"] = other_id
        with pytest.raises(ValueError, match="no matching strength log"):
            ex.restore_json(s, json.dumps(forged), owner)
        own = sp.list_detailed_logs(s, owner)
        assert len(own) == 1 and own[0][0].id == own_id
        assert len(sp.list_detailed_logs(s, other)) == 1

        malformed = json.loads(ex.to_json(s, owner))
        malformed["tables"]["strength_set_details"][0]["weight_kg"] = 10.05
        with pytest.raises(ValueError, match="outside supported bounds"):
            ex.restore_json(s, json.dumps(malformed), owner)
        assert len(sp.list_detailed_logs(s, owner)) == 1


def test_create_all_adds_detail_table_to_existing_sqlite_database(tmp_path):
    from sqlalchemy import create_engine, text

    db_file = tmp_path / "legacy.sqlite"
    legacy_engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    with legacy_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, "
            "auth_uid VARCHAR(100), email VARCHAR(320), created_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE strength_logs (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "date DATE NOT NULL, exercise VARCHAR NOT NULL, weight_kg FLOAT NOT NULL, "
            "sets INTEGER NOT NULL, reps INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id))"
        ))
        connection.execute(text(
            "INSERT INTO users (id, name, created_at) VALUES (1, 'Legacy', '2026-10-15 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO strength_logs VALUES (1, 1, '2026-10-15', 'Old Aggregate', 40, 3, 8)"
        ))
    legacy_engine.dispose()

    configure(f"sqlite:///{db_file.as_posix()}")
    with session_scope() as s:
        assert inspect(s.get_bind()).has_table("strength_set_details")
        assert s.get(StrengthLog, 1).exercise == "Old Aggregate"

    postgres_ddl = str(CreateTable(StrengthSetDetail.__table__).compile(
        dialect=postgresql.dialect()
    ))
    assert "FOREIGN KEY(user_id) REFERENCES users (id)" in postgres_ddl
    assert "FOREIGN KEY(strength_log_id) REFERENCES strength_logs (id)" in postgres_ddl


def test_detailed_entry_ui_keeps_correctable_draft_and_displays_sets(db, monkeypatch):
    from services import auth
    from utils import bootstrap
    import streamlit as st

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    app = AppTest.from_file(STRENGTH_PAGE).run()
    assert not app.exception
    app.text_input(key="strength_detail_0_exercise").set_value("  ")
    app.number_input(key="strength_detail_0_weight_0").set_value(73.4)
    app.number_input(key="strength_detail_0_reps_0").set_value(9)
    app.button(key="save_detailed_strength_log").click().run()
    assert not app.exception
    assert any("Exercise must not be blank" in error.value for error in app.error)
    assert app.text_input(key="strength_detail_0_exercise").value == "  "
    assert app.number_input(key="strength_detail_0_weight_0").value == 73.4
    assert app.number_input(key="strength_detail_0_reps_0").value == 9

    app.text_input(key="strength_detail_0_exercise").set_value("Bench Press")
    app.button(key="strength_detail_add_set").click().run()
    assert not app.exception
    app.number_input(key="strength_detail_0_weight_1").set_value(75.1)
    app.number_input(key="strength_detail_0_reps_1").set_value(7)
    app.button(key="save_detailed_strength_log").click().run()
    assert not app.exception
    assert any("Set 1: 73.4 kg × 9 reps" in item.value for item in app.markdown)
    assert any("Set 2: 75.1 kg × 7 reps" in item.value for item in app.markdown)
    with session_scope() as s:
        user = ps.ensure_user(s)
        log_id = sp.list_detailed_logs(s, user)[0][0].id
    app.button(key=f"edit_strength_log_{log_id}").click().run()
    assert not app.exception
    assert app.number_input(key=f"strength_detail_edit_weight_{log_id}_0").value == 73.4
    app.number_input(key=f"strength_detail_edit_weight_{log_id}_0").set_value(74.2)
    app.button(key=f"save_strength_detail_{log_id}").click().run()
    assert not app.exception
    with session_scope() as s:
        user = ps.ensure_user(s)
        detail_rows = sp.list_detailed_logs(s, user)[0][1]
        assert [(row.weight_kg, row.reps) for row in detail_rows] == [
            (74.2, 9), (75.1, 7)
        ]
        frame = sp.log_frame(s, user)
        assert frame.iloc[0]["est_1rm"] == 92.6

    captions = "\n".join(item.value for item in app.caption)
    assert "Estimated 1RM uses the Epley formula." in captions
    assert "highest-weight set; ties use the set with the most reps." in captions
    assert "No separately recorded tested 1RM is available." in captions
    assert "not verified tests." in captions
    assert "Best estimated 1RM (kg)" in app.dataframe[0].value.columns


def test_settings_shows_format_specific_manifests_before_downloads(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    settings_page = Path(__file__).resolve().parent.parent / "pages" / "settings.py"
    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    app = AppTest.from_file(settings_page).run()
    assert not app.exception
    manifest_text = "\n".join(item.value for item in app.markdown)
    assert "CSV schema v1 (selected table: users)" in manifest_text
    assert "EXCEL schema v1" in manifest_text
    assert "JSON schema v1" in manifest_text
    assert "Current signed-in account only; no other accounts." in manifest_text
    assert "Garmin source and activity IDs are excluded." in manifest_text
    assert "JSON restore replaces only the current account's exported data." in manifest_text
    assert "Saved Garmin credential settings are excluded and remain preserved by restore." in (
        manifest_text
    )
