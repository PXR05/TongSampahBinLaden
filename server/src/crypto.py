import base64
from dotenv import load_dotenv
import json
import os
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# AES-256 key (32 bytes)
_DEFAULT_AES_KEY = bytes(
    [
        0x2B,
        0x7E,
        0x15,
        0x16,
        0x28,
        0xAE,
        0xD2,
        0xA6,
        0xAB,
        0xF7,
        0x15,
        0x88,
        0x09,
        0xCF,
        0x4F,
        0x3C,
        0x2B,
        0x7E,
        0x15,
        0x16,
        0x28,
        0xAE,
        0xD2,
        0xA6,
        0xAB,
        0xF7,
        0x15,
        0x88,
        0x09,
        0xCF,
        0x4F,
        0x3C,
    ]
)

aes_key = _DEFAULT_AES_KEY

AES_BLOCK_SIZE = 16  # AES block size in bytes
AES_IV_SIZE = 16  # Initialization vector size in bytes


def encrypt_data(plaintext: str) -> str:
    if not plaintext:
        return ""

    try:
        # Convert string to bytes
        plaintext_bytes = plaintext.encode("utf-8")

        # Generate random IV
        iv = os.urandom(AES_IV_SIZE)

        # Add PKCS7 padding
        padder = padding.PKCS7(AES_BLOCK_SIZE * 8).padder()
        padded_data = padder.update(plaintext_bytes)
        padded_data += padder.finalize()

        # Encrypt using AES-256-CBC
        cipher = Cipher(
            algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend()
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        # Combine IV + ciphertext
        combined = iv + ciphertext

        # Base64 encode for transport
        return base64.b64encode(combined).decode("ascii")

    except Exception as e:
        raise ValueError(f"Encryption failed: {e}")


def decrypt_data(ciphertext: str) -> str:
    if not ciphertext:
        return ""

    try:
        # Base64 decode
        combined = base64.b64decode(ciphertext.encode("ascii"))

        # Ensure minimum length (IV + at least one block)
        if len(combined) < AES_IV_SIZE + AES_BLOCK_SIZE:
            raise ValueError("Ciphertext too short")

        # Extract IV and encrypted data
        iv = combined[:AES_IV_SIZE]
        encrypted_data = combined[AES_IV_SIZE:]

        # Decrypt using AES-256-CBC
        cipher = Cipher(
            algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend()
        )
        decryptor = cipher.decryptor()
        padded_plaintext = decryptor.update(encrypted_data) + decryptor.finalize()

        # Remove PKCS7 padding
        unpadder = padding.PKCS7(AES_BLOCK_SIZE * 8).unpadder()
        plaintext_bytes = unpadder.update(padded_plaintext)
        plaintext_bytes += unpadder.finalize()

        # Convert back to string
        return plaintext_bytes.decode("utf-8")

    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")


def encrypt_json(data: dict[str, Any]) -> str:  # pyright: ignore[reportExplicitAny]
    json_str = json.dumps(data, separators=(",", ":"))
    return encrypt_data(json_str)


def decrypt_json(ciphertext: str) -> dict[str, Any] | None:  # pyright: ignore[reportExplicitAny]
    try:
        plaintext = decrypt_data(ciphertext)
        return json.loads(plaintext)  # pyright: ignore[reportAny]
    except (ValueError, json.JSONDecodeError):
        return None


def create_encrypted_response(data: dict[str, Any]) -> dict[str, str]:  # pyright: ignore[reportExplicitAny]
    return {"encrypted": encrypt_json(data)}


def extract_encrypted_data(request_data: dict[str, Any]) -> dict[str, Any] | None:  # pyright: ignore[reportExplicitAny]
    if "encrypted" not in request_data:
        return None

    return decrypt_json(request_data["encrypted"])  # pyright: ignore[reportAny]


success = load_dotenv()
keyHex = os.getenv("AES_KEY_HEX")
if success and keyHex is not None:
    try:
        aes_key = bytes.fromhex(keyHex)
        if len(aes_key) != 32:
            raise ValueError("AES key must be 32 bytes (64 hex characters)")
    except ValueError as e:
        print(f"Warning: Invalid AES_KEY_HEX environment variable: {e}")
        print("Using default key (not secure for production)")
