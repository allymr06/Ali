from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Protocol


class CredentialError(RuntimeError):
    """Raised when the operating-system credential vault cannot be used."""


class CredentialStore(Protocol):
    """Minimal secret-store contract used by the desktop configuration UI."""

    def read(self) -> str | None: ...

    def write(self, secret: str) -> None: ...

    def delete(self) -> bool: ...


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)


class WindowsCredentialStore:
    """Store one API secret in the current user's Windows Credential Manager."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168
    MAX_SECRET_BYTES = 2560

    def __init__(self, target_name: str = "JARVIS/OpenAI API") -> None:
        normalized = target_name.strip()
        if not normalized:
            raise ValueError("Credential target name cannot be empty.")
        if os.name != "nt":
            raise OSError("Windows Credential Manager requires Windows.")
        self.target_name = normalized
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._configure_api()

    def _configure_api(self) -> None:
        self._advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_PCREDENTIALW),
        ]
        self._advapi32.CredReadW.restype = wintypes.BOOL
        self._advapi32.CredWriteW.argtypes = [
            ctypes.POINTER(_CREDENTIALW),
            wintypes.DWORD,
        ]
        self._advapi32.CredWriteW.restype = wintypes.BOOL
        self._advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi32.CredDeleteW.restype = wintypes.BOOL
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi32.CredFree.restype = None

    def read(self) -> str | None:
        pointer = _PCREDENTIALW()
        if not self._advapi32.CredReadW(
            self.target_name,
            self.CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            error = ctypes.get_last_error()
            if error == self.ERROR_NOT_FOUND:
                return None
            raise CredentialError(
                f"Windows Credential Manager read failed ({error})."
            )
        try:
            credential = pointer.contents
            if not credential.CredentialBlobSize:
                return None
            raw = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            return raw.decode("utf-16-le")
        finally:
            self._advapi32.CredFree(pointer)

    def write(self, secret: str) -> None:
        normalized = secret.strip()
        if not normalized:
            raise ValueError("API key cannot be empty.")
        encoded = normalized.encode("utf-16-le")
        if len(encoded) > self.MAX_SECRET_BYTES:
            raise ValueError("API key is too large for Windows Credential Manager.")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _CREDENTIALW()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = self.target_name
        credential.Comment = f"JARVIS credential for {self.target_name}"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(
            blob,
            ctypes.POINTER(ctypes.c_ubyte),
        )
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "JARVIS"
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise CredentialError(
                f"Windows Credential Manager write failed ({error})."
            )

    def delete(self) -> bool:
        if self._advapi32.CredDeleteW(
            self.target_name,
            self.CRED_TYPE_GENERIC,
            0,
        ):
            return True
        error = ctypes.get_last_error()
        if error == self.ERROR_NOT_FOUND:
            return False
        raise CredentialError(
            f"Windows Credential Manager delete failed ({error})."
        )
