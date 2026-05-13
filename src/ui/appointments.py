from __future__ import annotations
from nicegui import ui

from src.db.patient_repository import patient_repo
from src.db.appointment_repository import appointment_repo



def create() -> None:
    @ui.page("/appointments")
    def appointments_page():
        ui.label("Appointments Manager").classes("text-2xl font-bold mb-4")
        ui.link("← Dashboard", "/").classes("text-blue-600 underline text-sm mb-4 block")

        with ui.tabs().classes("w-full") as tabs:
            tab_appts = ui.tab("Appointments")
            tab_patients = ui.tab("Patients")
            tab_providers = ui.tab("Providers")

        with ui.tab_panels(tabs, value=tab_appts).classes("w-full"):

            # ── Appointments tab ──────────────────────────────────────────
            with ui.tab_panel(tab_appts):
                status_filter = ui.select(
                    ["all", "booked", "available", "cancelled", "completed"],
                    value="all",
                    label="Filter by status",
                ).classes("w-48 mb-4")

                appt_table = ui.table(
                    columns=[
                        {"name": "patient_name", "label": "Patient", "field": "patient_name", "align": "left", "sortable": True},
                        {"name": "provider_name", "label": "Provider", "field": "provider_name", "align": "left", "sortable": True},
                        {"name": "start_time", "label": "Start", "field": "start_time", "align": "left", "sortable": True},
                        {"name": "end_time", "label": "End", "field": "end_time", "align": "left"},
                        {"name": "status", "label": "Status", "field": "status", "align": "left", "sortable": True},
                        {"name": "notes", "label": "Notes", "field": "notes", "align": "left"},
                    ],
                    rows=[],
                    row_key="row_id",
                    pagination={"rowsPerPage": 15},
                ).classes("w-full").props("flat bordered selection=single")

                def load_appts():
                    rows = appointment_repo.get_all()
                    f = status_filter.value
                    if f != "all":
                        rows = [r for r in rows if r["status"] == f]
                    appt_table.rows = [
                        {**r, "row_id": r["id"],
                         "patient_name": r.get("patient_name") or "",
                         "provider_name": r.get("provider_name") or ""}
                        for r in rows
                    ]
                    appt_table.update()

                status_filter.on("update:model-value", lambda _: load_appts())
                load_appts()

                # Edit / cancel selected row
                selected_appt_id: dict = {"value": None}

                def on_appt_select(e):
                    selected_appt_id["value"] = e.args["rows"][0].get("row_id") if e.args and e.args.get("rows") else None

                appt_table.on("selection", on_appt_select)

                with ui.row().classes("gap-4 mt-4 items-end"):
                    edit_status = ui.select(
                        ["booked", "cancelled", "completed"],
                        label="New status",
                    ).classes("w-44")
                    edit_notes = ui.input(label="Notes").classes("w-64")

                    def save_appt():
                        aid = selected_appt_id["value"]
                        if not aid:
                            ui.notify("Select a row first", type="warning")
                            return
                        fields = {}
                        if edit_status.value:
                            fields["status"] = edit_status.value
                        if edit_notes.value:
                            fields["notes"] = edit_notes.value
                        if fields:
                            appointment_repo.update(aid, fields)
                            load_appts()
                            ui.notify("Appointment updated")

                    def cancel_appt():
                        aid = selected_appt_id["value"]
                        if not aid:
                            ui.notify("Select a row first", type="warning")
                            return
                        appointment_repo.cancel(aid)
                        load_appts()
                        ui.notify("Appointment cancelled")

                    ui.button("Save changes", on_click=save_appt)
                    ui.button("Cancel appointment", on_click=cancel_appt).props("color=negative")

            # ── Patients tab ──────────────────────────────────────────────
            with ui.tab_panel(tab_patients):
                patient_table = ui.table(
                    columns=[
                        {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
                        {"name": "phone", "label": "Phone", "field": "phone", "align": "left"},
                        {"name": "email", "label": "Email", "field": "email", "align": "left"},
                    ],
                    rows=[],
                    row_key="row_id",
                    pagination={"rowsPerPage": 15},
                ).classes("w-full").props("flat bordered selection=single")

                def load_patients():
                    patient_table.rows = [
                        {**p.model_dump(), "row_id": p.id}
                        for p in patient_repo.get_all()
                    ]
                    patient_table.update()

                load_patients()

                selected_patient_id: dict = {"value": None}

                def on_patient_select(e):
                    selected_patient_id["value"] = e.args["rows"][0].get("row_id") if e.args and e.args.get("rows") else None

                patient_table.on("selection", on_patient_select)

                ui.label("Add / edit patient").classes("text-lg font-semibold mt-6 mb-2")
                with ui.row().classes("gap-4 items-end"):
                    p_name = ui.input(label="Name").classes("w-48")
                    p_phone = ui.input(label="Phone (E.164)").classes("w-44")
                    p_email = ui.input(label="Email (optional)").classes("w-56")

                    def add_patient():
                        if not p_name.value or not p_phone.value:
                            ui.notify("Name and phone are required", type="warning")
                            return
                        try:
                            patient_repo.create(p_name.value, p_phone.value, p_email.value or None)
                            p_name.value = p_phone.value = p_email.value = ""
                            load_patients()
                            ui.notify("Patient added")
                        except Exception as exc:
                            ui.notify(f"Error: {exc}", type="negative")

                    def update_patient():
                        pid = selected_patient_id["value"]
                        if not pid:
                            ui.notify("Select a row first", type="warning")
                            return
                        patient_repo.update(pid, name=p_name.value or None, email=p_email.value or None)
                        p_name.value = p_email.value = ""
                        load_patients()
                        ui.notify("Patient updated")

                    ui.button("Add new", on_click=add_patient)
                    ui.button("Update selected", on_click=update_patient)

            # ── Providers tab ─────────────────────────────────────────────
            with ui.tab_panel(tab_providers):
                provider_table = ui.table(
                    columns=[
                        {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
                        {"name": "specialty", "label": "Specialty", "field": "specialty", "align": "left", "sortable": True},
                    ],
                    rows=[],
                    row_key="row_id",
                    pagination={"rowsPerPage": 15},
                ).classes("w-full").props("flat bordered selection=single")

                def load_providers():
                    provider_table.rows = [
                        {"row_id": p["id"], "name": p["name"], "specialty": p.get("specialty") or ""}
                        for p in appointment_repo.get_all_providers()
                    ]
                    provider_table.update()

                load_providers()

                selected_provider_id: dict = {"value": None}

                def on_provider_select(e):
                    selected_provider_id["value"] = e.args["rows"][0].get("row_id") if e.args and e.args.get("rows") else None

                provider_table.on("selection", on_provider_select)

                ui.label("Add / edit provider").classes("text-lg font-semibold mt-6 mb-2")
                with ui.row().classes("gap-4 items-end"):
                    prov_name = ui.input(label="Provider name").classes("w-56")
                    prov_specialty = ui.input(label="Specialty (optional)").classes("w-56")

                    def add_provider():
                        if not prov_name.value:
                            ui.notify("Name is required", type="warning")
                            return
                        appointment_repo.create_provider(prov_name.value, prov_specialty.value or None)
                        prov_name.value = prov_specialty.value = ""
                        load_providers()
                        ui.notify("Provider added")

                    def update_provider():
                        pid = selected_provider_id["value"]
                        if not pid:
                            ui.notify("Select a row first", type="warning")
                            return
                        if not prov_name.value:
                            ui.notify("Enter a new name", type="warning")
                            return
                        appointment_repo.update_provider(pid, prov_name.value, prov_specialty.value or None)
                        prov_name.value = prov_specialty.value = ""
                        load_providers()
                        ui.notify("Provider updated")

                    def delete_provider():
                        pid = selected_provider_id["value"]
                        if not pid:
                            ui.notify("Select a row first", type="warning")
                            return
                        appointment_repo.delete_provider(pid)
                        load_providers()
                        ui.notify("Provider deleted")

                    ui.button("Add new", on_click=add_provider)
                    ui.button("Update selected", on_click=update_provider)
                    ui.button("Delete selected", on_click=delete_provider).props("color=negative")
