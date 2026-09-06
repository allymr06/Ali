"""JARVIS Medical Academy: a first-year medical study layer.

Curriculum, terminology, concept graph, document engine, tutor,
question engine, professor-style profiling, learning model and the
Anatomy Lab, all behind one facade (:class:`MedicalAcademy`) that the
core engine, the tools and the Nova shell talk to.
"""

from app.medical.academy import MedicalAcademy, create_medical_academy
from app.medical.intents import MedicalIntent, MedicalIntentParser, StudyCommand

__all__ = [
    "MedicalAcademy",
    "MedicalIntent",
    "MedicalIntentParser",
    "StudyCommand",
    "create_medical_academy",
]
