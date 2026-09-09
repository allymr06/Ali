"""Reviewed atlas allowlist. No inner-ear/kidney assets or embedded scripts."""

REVISION = "b22c56c340eaf72d8031e5c364b6ceb38da44cd6"
ARCHIVE_SHA256 = "e029688545627bd0214b269e1063143abb580aad72b2c2445d6d8a9a0d9da736"
BLEND_SHA256 = "9f08a17ea0115fed80b2a73ecdf0a1bc2ab2f6956f37c593ce23d513ea35afcd"
SOURCE = f"https://github.com/Z-Anatomy/Models-of-human-anatomy/tree/{REVISION}"
ARCHIVE_URL = f"https://raw.githubusercontent.com/Z-Anatomy/Models-of-human-anatomy/{REVISION}/Z-Anatomy.zip"
LICENSE = "CC BY-SA 4.0; underlying BodyParts3D attribution CC BY-SA 2.1 Japan"
ATTRIBUTION = (
    "Z-Anatomy — The libre 3D atlas of anatomy — CC BY-SA 4.0; "
    "BodyParts3D — The Database Center for Life Science — CC BY-SA 2.1 Japan. "
    "Adaptation: selected right-side objects, world-space triangulated OBJ export, "
    "source annotation endpoints projected to bone surfaces."
)
FULL_ATTRIBUTION = ATTRIBUTION.replace("selected right-side objects", "selected bilateral and unpaired objects") + " Cranial nerves: Cranial Nerves and Foramina — University of Dundee, CAHID — CC BY 4.0."

UPPER = {
    "scapula": ["Scapula.r"], "clavicula": ["Clavicle.r"],
    "humerus": ["Humerus.r"], "radius": ["Radius.r"], "ulna": ["Ulna.r"],
    "m_deltoideus": ["Clavicular part of deltoid muscle.r", "Acromial part of deltoid muscle.r", "Scapular spinal part of deltoid muscle.r"],
    "m_supraspinatus": ["Supraspinatus muscle.r"], "m_infraspinatus": ["Infraspinatus muscle.r"],
    "m_teres_minor": ["Teres minor muscle.r"], "m_teres_major": ["Teres major muscle.r"],
    "m_subscapularis": ["Subscapularis muscle.r"],
    "m_biceps_brachii": ["Long head of biceps brachii.r", "Short head of biceps brachii.r"],
    "m_coracobrachialis": ["Coracobrachialis muscle.r"], "m_brachialis": ["Brachialis muscle.r"],
    "m_triceps_brachii": ["Long head of triceps brachii.r", "Lateral head of triceps brachii.r", "Medial head of triceps brachii.r"],
    "m_brachioradialis": ["Brachioradialis muscle.r"],
    "m_pectoralis_major": ["Clavicular head of pectoralis major muscle.r", "Sternocostal head of pectoralis major muscle.r", "(Abdominal part of pectoralis major muscle).r"],
    "m_pectoralis_minor": ["Pectoralis minor muscle.r"], "m_serratus_anterior": ["Serratus anterior muscle.r"],
    "a_axillaris": ["Axillary artery.r"], "a_brachialis": ["Brachial artery.r"],
    "a_radialis": ["Radial artery.r"], "a_ulnaris": ["Ulnar artery.r"],
    "v_cephalica": ["Cephalic vein.r"], "v_basilica": ["Basilic vein.r"],
    "n_axillaris": ["Axillary nerve.r"], "n_suprascapularis": ["Suprascapular nerve.r"],
    "n_musculocutaneus": ["Musculocutaneous nerve.r"], "n_radialis": ["Radial nerve.r"],
    "n_thoracicus_longus": ["Long thoracic nerve.r"],
}
LOWER = {
    "femur": ["Femur.r"], "tibia": ["Tibia.r"], "fibula": ["Fibula.r"],
    "patella": ["Patella.r"], "n_femoralis": ["Femoral nerve.r"],
    "n_obturatorius": ["Obturator nerve.r"], "n_tibialis": ["Tibial nerve.r"],
}
OBJECTS = {**UPPER, **LOWER}

# .j annotations in this pinned atlas refer to the right bones. The exporter
# validates the hook target when present AND surface distance. Femur markers
# are source-authored world-space pointers without hooks in this revision.
LANDMARKS = {
    "humerus": {
        "tuberculum_majus": "Greater tubercle.j", "tuberculum_minus": "Lesser tubercle.j",
        "fossa_olecrani": "Olecranon fossa.j", "fossa_coronoidea": "Coronoid fossa.j",
        "fossa_radialis": "Radial fossa.j", "sulcus_intertubercularis": "Intertubercular sulcus.j",
    },
    "scapula": {
        "fossa_supraspinata": "Supraspinous fossa.j", "fossa_infraspinata": "Infraspinous fossa.j",
        "fossa_subscapularis": "Subscapular fossa.j", "tuberculum_supraglenoidale": "Supraglenoid tubercle.j",
        "tuberculum_infraglenoidale": "Infraglenoid tubercle.j",
    },
    "clavicula": {"tuberculum_conoideum": "Conoid tubercle.j"},
    "femur": {
        "fovea_capitis_femoris": "Fovea for ligament of head of femur.j",
        "tuberculum_adductorium": "Adductor tubercle.j", "fossa_intercondylaris": "Intercondylar fossa.j",
    },
}

SCENES = [
    {"scene_id": "upper_limb_right", "title": "Üst ekstremite · sağ", "region": "upper_limb", "structure_ids": list(UPPER)},
    {"scene_id": "lower_limb_right", "title": "Alt ekstremite · sağ", "region": "lower_limb", "structure_ids": list(LOWER)},
]
for scene in SCENES:
    scene["note"] = "Z-Anatomy · sağ taraf · sinirleri görmek için kas katmanını kapatabilirsiniz. İnce ayrıntılar kaynak çözünürlüğüyle sınırlıdır."
