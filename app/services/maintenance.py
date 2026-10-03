"""Service intervals and service events for the dealer network."""

import logging
import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FaultCode, Machine, ServiceEvent, ServiceEventKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ServiceInterval:
    hours: int
    title: str


# Largest first: a 1000 h service also covers the 500 h and 250 h work.
SERVICE_INTERVALS = (
    ServiceInterval(2000, "2000 h service: hydraulic oil, coolant, swing and travel gear oil"),
    ServiceInterval(1000, "1000 h service: hydraulic return filter, fuel system, belts"),
    ServiceInterval(500, "500 h service: engine oil, fuel and air filters"),
    ServiceInterval(250, "250 h service: engine oil and oil filter"),
)


def due_interval(previous_hours: float, current_hours: float) -> ServiceInterval | None:
    """Return the largest interval whose boundary lies in ``(previous, current]``."""
    for interval in SERVICE_INTERVALS:
        if math.floor(current_hours / interval.hours) > math.floor(previous_hours / interval.hours):
            return interval
    return None


def check_service_interval(
    session: AsyncSession, machine: Machine, previous_hours: float
) -> list[ServiceEvent]:
    interval = due_interval(previous_hours, machine.engine_hours)
    if interval is None:
        return []
    event = ServiceEvent(
        machine_id=machine.id,
        kind=ServiceEventKind.SCHEDULED_SERVICE,
        title=interval.title,
        engine_hours=machine.engine_hours,
        details={"interval_hours": interval.hours},
    )
    session.add(event)
    logger.info(
        "Machine %s due for %d h service at %.1f h",
        machine.id,
        interval.hours,
        machine.engine_hours,
    )
    return [event]


def open_fault_event(session: AsyncSession, machine: Machine, fault: FaultCode) -> ServiceEvent:
    event = ServiceEvent(
        machine_id=machine.id,
        fault_code_id=fault.id,
        kind=ServiceEventKind.FAULT,
        title=f"Critical fault SPN {fault.spn} FMI {fault.fmi}",
        engine_hours=machine.engine_hours,
        details={
            "spn": fault.spn,
            "fmi": fault.fmi,
            "source_address": fault.source_address,
            "occurrence_count": fault.occurrence_count,
        },
    )
    session.add(event)
    logger.info("Machine %s raised critical fault %s/%s", machine.id, fault.spn, fault.fmi)
    return event
