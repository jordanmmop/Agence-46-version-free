"""Téléchargements HTTPS fiables, y compris depuis un exe Windows.

Le symptôme corrigé ici :

    SSL: CERTIFICATE_VERIFY_FAILED
    unable to get local issuer certificate (_ssl.c:1082)

Il ne veut pas dire que le site est en panne, mais que Python n'a pas réussi
à VÉRIFIER son certificat, faute de trouver l'autorité qui l'a signé. Trois
causes, très courantes sous Windows, et aucune n'est la faute de
l'utilisateur :

1. **Un antivirus ou un pare-feu d'entreprise inspecte le HTTPS.** Il
   remplace le certificat du site par le sien, signé par une autorité que
   Windows connaît mais que Python, lui, ignore.
2. **L'exe compilé n'embarque aucun magasin de certificats.** Python s'appuie
   alors sur celui de Windows, qui ne télécharge ses intermédiaires qu'à la
   demande — et échoue quand il n'y arrive pas.
3. **Un proxy système** détourne la connexion.

On essaie donc les sources de confiance L'UNE APRÈS L'AUTRE, de la plus
adaptée à la machine à la plus universelle :

  1. le magasin de Windows/macOS via `truststore` — seul capable d'accepter
     l'autorité d'un antivirus, puisqu'elle y est installée ;
  2. le paquet `certifi`, embarqué dans l'exe — indépendant du système ;
  3. la configuration par défaut de Python.

La vérification n'est JAMAIS désactivée : ces fichiers sont des programmes
que l'utilisateur va exécuter, et accepter n'importe quel certificat
reviendrait à accepter n'importe quel programme.
"""
import logging
import ssl
import urllib.request
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Nom de la source de confiance qui a fonctionné la dernière fois : les
# téléchargements suivants commencent par elle au lieu de refaire tous les
# essais (un modèle IA se télécharge par tranches, l'échec coûterait cher).
_source_qui_marche: Optional[str] = None


def _contexte_truststore() -> Optional[ssl.SSLContext]:
    """Magasin de certificats du SYSTÈME (Windows, macOS).

    C'est la seule source qui contienne l'autorité d'un antivirus inspectant
    le HTTPS — cas n°1 des échecs de vérification sur un poste Windows.
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return None


def _contexte_certifi() -> Optional[ssl.SSLContext]:
    """Paquet de certificats `certifi`, embarqué dans l'application.

    Indépendant du système : c'est lui qui sauve un exe compilé sur une
    machine dont le magasin Windows est incomplet.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _contexte_defaut() -> Optional[ssl.SSLContext]:
    try:
        return ssl.create_default_context()
    except Exception:
        return None


_SOURCES: List[tuple] = [
    ("magasin du système", _contexte_truststore),
    ("certificats embarqués (certifi)", _contexte_certifi),
    ("configuration par défaut", _contexte_defaut),
]


def _openers() -> List[tuple]:
    """Openers à essayer, dans l'ordre — celui qui a marché en premier."""
    sources = list(_SOURCES)
    if _source_qui_marche:
        sources.sort(key=lambda s: s[0] != _source_qui_marche)
    openers = []
    for nom, fabrique in sources:
        contexte = fabrique()
        if contexte is None:
            continue
        # ProxyHandler({}) : ne PAS passer par le proxy système. Les appels
        # visent soit 127.0.0.1 (moteurs locaux), soit des CDN publics ; un
        # proxy d'entreprise mal configuré casserait les deux.
        openers.append((nom, urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=contexte))))
    if not openers:
        openers.append(("sans contexte", urllib.request.build_opener(
            urllib.request.ProxyHandler({}))))
    return openers


def ouvrir(url_ou_requete, timeout: float = 60):
    """`urlopen` qui essaie chaque source de confiance jusqu'à en trouver une.

    Rejoue UNIQUEMENT sur un échec de vérification de certificat : une erreur
    404 ou une coupure réseau n'a rien à voir avec la confiance, la relancer
    avec un autre magasin ne ferait que masquer la vraie cause.
    """
    global _source_qui_marche
    derniere_ssl = None
    for nom, opener in _openers():
        try:
            reponse = opener.open(url_ou_requete, timeout=timeout)
            if _source_qui_marche != nom:
                logger.info(f"[réseau] Certificats validés via : {nom}")
                _source_qui_marche = nom
            return reponse
        except ssl.SSLError as e:
            derniere_ssl = e
            logger.debug(f"[réseau] {nom} : vérification refusée ({e})")
            continue
        except urllib.error.URLError as e:
            # URLError enveloppe souvent l'erreur SSL : on ne réessaie que
            # dans ce cas, pas sur un « connexion refusée ».
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                derniere_ssl = e.reason
                logger.debug(f"[réseau] {nom} : vérification refusée ({e.reason})")
                continue
            raise
    raise CertificatIntrouvable(derniere_ssl)


class CertificatIntrouvable(Exception):
    """Aucune source de confiance n'a pu valider le certificat du serveur.

    Porte un message qui NOMME les causes probables et ce qu'il y a à faire :
    « certificate verify failed » seul n'aide personne.
    """

    def __init__(self, cause=None):
        self.cause = cause
        super().__init__(
            "Le certificat du site n'a pas pu être vérifié. C'est presque "
            "toujours un antivirus ou un pare-feu qui inspecte les connexions "
            "HTTPS, ou un magasin de certificats Windows incomplet. À "
            "essayer, dans l'ordre : mettre Windows à jour (les certificats "
            "en font partie) ; désactiver l'analyse HTTPS de l'antivirus le "
            "temps du téléchargement ; ou télécharger le fichier vous-même "
            f"depuis votre navigateur. (détail technique : {cause})")


def telecharger(url: str, cible, timeout: float = 60,
                progression: Callable[[float], None] = None,
                agent: str = "AgenceNumerique") -> None:
    """Télécharge `url` vers `cible`, avec contrôle de complétude.

    Le fichier n'est mis en place qu'une fois COMPLET (écriture dans un
    « .part » puis renommage) : une coupure ne laisse jamais un installeur ou
    un modèle tronqué que l'application prendrait pour valide.
    """
    from pathlib import Path
    cible = Path(cible)
    cible.parent.mkdir(parents=True, exist_ok=True)
    part = cible.with_suffix(cible.suffix + ".part")
    requete = urllib.request.Request(url, headers={"User-Agent": agent})
    try:
        with ouvrir(requete, timeout=timeout) as reponse, open(part, "wb") as f:
            total = int(reponse.headers.get("Content-Length") or 0)
            lu = 0
            while True:
                bloc = reponse.read(1024 * 256)
                if not bloc:
                    break
                f.write(bloc)
                lu += len(bloc)
                if total and progression:
                    progression(lu / total * 100)
        if total and lu < total:
            raise RuntimeError(f"téléchargement incomplet ({lu}/{total} octets)")
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.replace(cible)
