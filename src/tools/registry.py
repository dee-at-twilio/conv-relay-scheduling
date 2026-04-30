from src.tools.base import ToolRegistry
from src.tools.cancel_appointment import CancelAppointmentTool
from src.tools.check_availability import CheckAvailabilityTool
from src.tools.get_patient_appointments import GetPatientAppointmentsTool
from src.tools.patient_lookup import PatientLookupTool
from src.tools.reschedule_appointment import RescheduleAppointmentTool
from src.tools.schedule_appointment import ScheduleAppointmentTool

tool_registry = ToolRegistry()
tool_registry.register(PatientLookupTool())
tool_registry.register(GetPatientAppointmentsTool())
tool_registry.register(CheckAvailabilityTool())
tool_registry.register(ScheduleAppointmentTool())
tool_registry.register(RescheduleAppointmentTool())
tool_registry.register(CancelAppointmentTool())
