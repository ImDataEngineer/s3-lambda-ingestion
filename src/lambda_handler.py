"""Sobral ingestion Lambda — S3 object-created -> DynamoDB rows + dead-letter quarantine.

Ce module est le SCAFFOLD que tu vas compléter. Il est packagé tel quel (zip)
puis déployé via Terraform comme la fonction Lambda `sobral_ingest`.

Flux attendu
------------

    S3 PutObject (CSV)              triggers
        s3://sobral-deliveries/incoming/foo.csv  -->  this Lambda
                                                            |
                                                            v
                              parse CSV row by row (streaming)
                                                            |
                                       valid rows ---> DynamoDB delivery_events
                                                       (batch_write_item, 25 max)
                                       malformed rows -> s3://sobral-deliveries/dead-letter/<key>.csv

Invariants
----------

1. **Pas de put_item dans une boucle.** `batch_write_item` accepte 25 items à
   la fois ; respecte cette limite ou tu prends une `ValidationException`.
2. **Re-traite les `UnprocessedItems`.** Sous throttling, DynamoDB renvoie une
   partie de tes items dans la clé `UnprocessedItems` — c'est PAS une erreur,
   c'est un signal "retry-moi ça plus tard". Boucle jusqu'à liste vide
   (avec un backoff borné, sinon tu boucles à l'infini sur une vraie panne).
3. **Une ligne malformée n'est PAS une exception.** Tu la mets de côté dans le
   dead-letter. Si tu fais `raise` sur la 1ʳᵉ ligne pourrie, S3 va re-livrer
   l'event indéfiniment (boucle infinie de retries Lambda) et tu n'auras
   jamais ingéré les 1198 bonnes lignes.
4. **Streaming, pas full-read.** Tu ne fais PAS
   `s3.get_object(...)["Body"].read()` puis `csv.reader(StringIO(...))`. Tu
   utilises `csv.DictReader(io.TextIOWrapper(s3_obj["Body"]))` pour ne pas
   monter 100 MB en mémoire sur un gros fichier.
5. **Clé primaire DynamoDB** : `(truck_id, delivery_id)`. Tu fais le cast en
   `int` côté truck_id sinon ton query plus tard ne matche pas.

Le handler doit retourner un dict (Lambda runtime le sérialise en JSON dans
les logs CloudWatch — utile pour observability).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from typing import Any, Iterable
from urllib.parse import unquote_plus

import boto3

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

# Quand la Lambda tourne dans le conteneur runtime LocalStack, la variable
# `AWS_ENDPOINT_URL` est injectée automatiquement (depuis la version
# LocalStack ≥ 2.0). Tu n'as donc PAS besoin de la passer à la main ici — le
# SDK boto3 1.34+ la lit. Si elle est absente (= vrai AWS), le SDK part en
# résolution de credential chain standard.
_ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL")

DDB_TABLE_NAME = os.environ.get("DDB_TABLE_NAME", "delivery_events")
DEAD_LETTER_PREFIX = os.environ.get("DEAD_LETTER_PREFIX", "dead-letter/")

# DynamoDB hard limit. Ne le déplace pas — c'est une contrainte du service.
DDB_BATCH_LIMIT = 25


def _s3_client():
    if _ENDPOINT_URL:
        return boto3.client("s3", endpoint_url=_ENDPOINT_URL)
    return boto3.client("s3")


def _ddb_client():
    if _ENDPOINT_URL:
        return boto3.client("dynamodb", endpoint_url=_ENDPOINT_URL)
    return boto3.client("dynamodb")


# ---------------------------------------------------------------------------
# Helpers à implémenter
# ---------------------------------------------------------------------------


def parse_row(raw: dict[str, str]) -> dict[str, Any] | None:
    """Convertit une ligne CSV brute en item DynamoDB valide.

    Retourne `None` si la ligne est malformée (lat/lon non-numériques, lat hors
    [-90, 90], lon hors [-180, 180], champ obligatoire manquant). NE LÈVE PAS
    d'exception — la ligne pourrie est une donnée comme une autre, on la met
    en quarantaine plus haut dans la pile.

    Format de retour attendu (clé partition = truck_id, clé tri = delivery_id) :

        {
            "truck_id":    {"N": "1001"},
            "delivery_id": {"S": "D2026041600001"},
            "ts":          {"S": "2026-04-16T05:30:00Z"},
            "lat":         {"N": "48.851234"},
            "lon":         {"N": "2.347891"},
            "status":      {"S": "delivered"},
            "weight_kg":   {"N": "12.50"},
        }
    """
    # TODO: valider que truck_id et delivery_id sont présents et non-vides.
    # TODO: caster lat / lon / weight_kg en float, retourner None si ValueError.
    # TODO: vérifier les bornes géo (lat in [-90, 90], lon in [-180, 180]).
    # TODO: convertir en dict DynamoDB AttributeValue (cf. format ci-dessus).
    raise NotImplementedError(
        "parse_row() pas encore implémenté — lis les TODOs et le format attendu."
    )


def chunked(items: list[dict[str, Any]], size: int = DDB_BATCH_LIMIT) -> Iterable[list[dict[str, Any]]]:
    """Découpe `items` en sous-listes de `size` éléments max."""
    # TODO: yield des slices de longueur ≤ size. Une seule ligne suffit.
    raise NotImplementedError("chunked() pas encore implémenté.")


def batch_write_with_retry(ddb, table_name: str, items: list[dict[str, Any]]) -> int:
    """Envoie `items` en DynamoDB via batch_write_item, ré-essaie les UnprocessedItems.

    Retourne le nombre d'items effectivement écrits.

    Implémentation conseillée :

        for chunk in chunked(items):
            req = {table_name: [{"PutRequest": {"Item": it}} for it in chunk]}
            while req:
                resp = ddb.batch_write_item(RequestItems=req)
                req = resp.get("UnprocessedItems") or {}
                if req:
                    time.sleep(...)   # backoff borné, capé à ~5 retries
    """
    # TODO: implémenter avec un compteur d'essais maximum pour éviter une
    #       boucle infinie en cas de throttling permanent (ex. capacité
    #       provisionnée trop basse).
    raise NotImplementedError("batch_write_with_retry() pas encore implémenté.")


def write_dead_letter(s3, bucket: str, source_key: str, malformed_rows: list[dict[str, str]]) -> str | None:
    """Écrit les lignes malformées dans s3://bucket/dead-letter/<basename>.csv.

    Retourne la clé S3 créée, ou None si la liste est vide (pas de dead-letter
    quand tout est propre, on ne pollue pas le bucket avec des fichiers vides).

    Le format du fichier dead-letter est un CSV avec EXACTEMENT le même header
    que le fichier source, plus une colonne `_error` en fin de ligne expliquant
    pourquoi la ligne a été rejetée (ça t'aide en debug — un humain doit
    pouvoir lire le dead-letter et comprendre).
    """
    # TODO: si malformed_rows est vide, return None.
    # TODO: sinon construire un CSV en mémoire (io.StringIO + csv.DictWriter)
    #       et faire un s3.put_object avec une clé déterministe basée sur le
    #       basename du fichier source.
    raise NotImplementedError("write_dead_letter() pas encore implémenté.")


# ---------------------------------------------------------------------------
# Handler principal
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context: Any) -> dict:
    """Point d'entrée AWS Lambda — déclenché par un PutObject S3.

    L'argument `event` a la forme :

        {
          "Records": [
            {
              "s3": {
                "bucket": {"name": "sobral-deliveries"},
                "object": {"key": "incoming/deliveries_2026-04-16.csv"}
              }
            }
          ]
        }

    Plusieurs Records peuvent arriver dans un même événement — itère tous, ne
    suppose pas Records[0] seul.

    Le handler doit :

      1. Pour chaque Record, télécharger l'objet en STREAMING (cf. invariant 4).
      2. Itérer ligne par ligne, classer en (valide, malformée).
      3. Envoyer les valides en DynamoDB via batch_write_with_retry.
      4. Écrire les malformées dans un objet dead-letter S3 si non-vide.
      5. Retourner un dict JSON-sérialisable récapitulatif (rows_total,
         rows_inserted, rows_malformed, dead_letter_key).
    """
    LOGGER.info("Received event: %s", json.dumps(event))
    s3 = _s3_client()
    ddb = _ddb_client()

    # TODO: pour chaque record S3 :
    #   - récupérer bucket / key (n'oublie pas urllib.parse.unquote_plus sur la key)
    #   - s3.get_object -> stream
    #   - csv.DictReader sur le stream
    #   - pour chaque row : parse_row -> valide ou malformée
    #   - batch_write_with_retry(valides)
    #   - write_dead_letter(malformées)
    # TODO: retourner un dict de stats.
    raise NotImplementedError(
        "lambda_handler() pas encore implémenté. Lis le scaffold et complète "
        "parse_row / chunked / batch_write_with_retry / write_dead_letter d'abord."
    )
