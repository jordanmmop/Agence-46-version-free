"""Serveur Novia FACTICE — implémentation de référence du contrat documenté.

Il applique exactement docs/novia-licensing-integration.md : mêmes routes,
mêmes corps, mêmes codes d'erreur, même jeton EdDSA. Il sert deux fois :

  - aux tests, qui l'utilisent comme transport en mémoire ou comme vrai
    serveur HTTPS (voir test_novia_licence.py) ;
  - au développeur du site Novia, comme exemple exécutable de ce que
    l'application attend.

Vit dans python/tests/, exclu du paquet distribué par agence.spec : la clé
PRIVÉE qu'il génère n'existe que le temps d'un test, et sur aucune machine
d'utilisateur.
"""
import json
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def b64e(donnees: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(donnees).decode("ascii").rstrip("=")


class NoviaFactice:
    PRODUIT = "agence46"

    def __init__(self, kid: str = "novia-test-1", horloge=time.time,
                 hors_ligne_jours: int = 30):
        self.cle_privee = Ed25519PrivateKey.generate()
        self.kid = kid
        self.horloge = horloge
        self.hors_ligne_jours = hors_ligne_jours
        self.licences: Dict[str, Dict[str, Any]] = {}
        self.requetes = []            # (chemin, corps) — pour les tests
        self.en_panne = False         # 503
        self.hors_ligne = False       # plus de réseau du tout

    # ── Clé publique à embarquer dans l'application ──
    def cle_publique_hex(self) -> str:
        return self.cle_privee.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    # ── Administration (côté site Novia) ──
    def creer_licence(self, niveau: str = "PRO", duree_jours: Optional[float] = 365,
                      max_appareils: int = 2, statut: str = "ACTIVE") -> str:
        groupes = ["".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
                           for _ in range(4)) for _ in range(4)]
        cle = "NOVIA-" + "-".join(groupes)
        self.licences[cle] = {
            "id": "lic_" + secrets.token_hex(6),
            "niveau": niveau, "statut": statut, "max": max_appareils,
            "expire": (self.horloge() + duree_jours * 86400) if duree_jours else None,
            "appareils": set(),
        }
        return cle

    # ── Jeton ──
    def signer(self, charge: Dict[str, Any], en_tete: Optional[Dict[str, Any]] = None,
               cle_privee=None) -> str:
        en_tete = en_tete or {"alg": "EdDSA", "typ": "JWT", "kid": self.kid}
        h = b64e(json.dumps(en_tete, separators=(",", ":")).encode())
        p = b64e(json.dumps(charge, separators=(",", ":")).encode())
        sig = (cle_privee or self.cle_privee).sign(f"{h}.{p}".encode("ascii"))
        return f"{h}.{p}.{b64e(sig)}"

    def jeton_pour(self, cle: str, installation: str, **surcharges) -> str:
        lic = self.licences[cle]
        maintenant = int(self.horloge())
        hors_ligne = maintenant + self.hors_ligne_jours * 86400
        if lic["expire"]:
            hors_ligne = min(hors_ligne, int(lic["expire"]))
        charge = {
            "iss": "novia", "aud": self.PRODUIT, "sub": lic["id"],
            "inst": installation, "tier": lic["niveau"], "status": "ACTIVE",
            "iat": maintenant,
            "exp": int(lic["expire"]) if lic["expire"] else None,
            "offline_until": hors_ligne,
            "key_hint": cle[-4:], "max_devices": lic["max"],
        }
        charge.update(surcharges)
        return self.signer(charge)

    # ── Le serveur ──
    def repondre(self, chemin: str, corps: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        self.requetes.append((chemin, dict(corps)))
        if self.en_panne:
            return 503, {"valid": False, "error_code": "SERVER_ERROR"}

        refus = lambda http, statut, code, msg="": (http, {
            "valid": False, "license_status": statut, "product": self.PRODUIT,
            "error_code": code, "message": msg, "server_time": int(self.horloge())})

        if corps.get("product") != self.PRODUIT:
            return refus(400, "INVALID", "PRODUCT_MISMATCH")
        cle = corps.get("license_key")
        inst = corps.get("installation_id")
        lic = self.licences.get(cle)
        if not lic:
            return refus(404, "INVALID", "INVALID_KEY")

        if chemin == "/api/license/deactivate":
            if inst not in lic["appareils"]:
                return refus(404, "INVALID", "INSTALLATION_NOT_FOUND")
            lic["appareils"].discard(inst)
            return 200, {"valid": True, "license_status": "ACTIVE",
                         "product": self.PRODUIT, "server_time": int(self.horloge())}

        statut = lic["statut"]
        if lic["expire"] and self.horloge() > lic["expire"]:
            statut = "EXPIRED"
        if statut != "ACTIVE":
            codes = {"EXPIRED": "LICENSE_EXPIRED", "SUSPENDED": "LICENSE_SUSPENDED",
                     "REVOKED": "LICENSE_REVOKED"}
            return refus(403, statut, codes[statut])

        if chemin == "/api/license/activate":
            if inst not in lic["appareils"] and len(lic["appareils"]) >= lic["max"]:
                return refus(403, "INVALID", "DEVICE_LIMIT_REACHED")
            lic["appareils"].add(inst)
        elif chemin == "/api/license/validate":
            if inst not in lic["appareils"]:
                return refus(404, "INVALID", "INSTALLATION_NOT_FOUND")
        else:
            return 404, {"valid": False, "error_code": "INVALID_REQUEST"}

        jeton = self.jeton_pour(cle, inst)
        charge = json.loads(__import__("base64").urlsafe_b64decode(
            jeton.split(".")[1] + "==").decode())
        return 200, {
            "valid": True, "license_status": "ACTIVE", "product": self.PRODUIT,
            "license_type": lic["niveau"], "expires_at": charge["exp"],
            "offline_until": charge["offline_until"], "signed_token": jeton,
            "server_time": int(self.horloge()),
        }

    # ── Transport en mémoire, branché sur NoviaClient ──
    def transport(self, url: str, corps: Dict[str, Any], timeout: float):
        if self.hors_ligne:
            from licence.novia_client import PasDeConnexion
            raise PasDeConnexion("Impossible de contacter le serveur de licences.")
        chemin = "/" + url.split("://", 1)[1].split("/", 1)[1]
        return self.repondre(chemin, corps)
