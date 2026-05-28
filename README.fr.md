# Ta première ingestion AWS, en local — `ingestion.s3-lambda-localstack`

> **Niveau** : junior · **Durée estimée** : ~10 h · **Projet payant IAmDataEng**
> **Axes framework** : `ingestion`, `software_engineering_dataops`

Tu vas construire une vraie pipeline AWS — S3 qui déclenche une Lambda qui
écrit dans DynamoDB — **sans compte AWS**, sans carte bancaire, sans
LocalStack Pro. Juste LocalStack Community, Terraform et boto3. Le tout
provisionné comme en prod (déclaratif, idempotent), testé comme en prod
(end-to-end, automatisé).

Ce n'est pas un tutoriel. Tu lis, tu codes, tu pousses, et la CI te dit si ça
passe — avec, à chaque échec, un message qui t'indique quoi corriger.

---

## Le contexte

**Sobral**, startup de logistique. Chaque tablette de chaque chauffeur dépose
en fin de tournée un CSV de livraisons sur S3. Le dispatch doit pouvoir, en
moins de 10 ms, retrouver toutes les livraisons d'un camion donné — c'est
DynamoDB qu'il leur faut, pas un data warehouse.

Aujourd'hui : un script Python tourne sur un EC2 que le lead doit SSH dedans
chaque matin. Le lead veut du **serverless**, et il veut que tu le câbles
proprement : Terraform, IAM scopé, gestion d'erreurs, idempotence.

Le piège : tu n'as pas de compte AWS et tu ne paieras pas pour en avoir un.
Tu vas construire **contre les APIs AWS** en utilisant LocalStack pour le
dev et la CI. Quand un recruteur te demandera "as-tu déjà fait du AWS ?",
tu pourras dire la vérité — *"j'ai construit contre les APIs AWS via
LocalStack, voici le repo, voici la rubric CI qui prouve que ça tourne"* —
au lieu de gonfler ton CV.

---

## Ce que tu vas livrer

| Livrable | Où |
|---|---|
| Le code de la Lambda | `src/lambda_handler.py` (handler S3 -> DynamoDB + dead-letter) |
| Le helper boto3 LocalStack-aware | `src/aws_config.py` (déjà fourni — relis-le) |
| L'infrastructure Terraform | `terraform/main.tf` (S3 + DynamoDB + IAM + Lambda + event notification) |
| La compose stack | `docker-compose.yml` (LocalStack Community, déjà fourni) |
| La fixture CSV | `fixtures/deliveries_2026-04-16.csv` (1200 lignes, 2 malformées, déjà fourni) |

Tu **ne touches pas** au générateur de fixture (`fixtures/generate_fixtures.py`)
— le seed est calibré pour que la rubric CI assertionne sur des chiffres
exacts (1198 lignes valides, 2 malformées). Si tu changes le seed, tes
checks divergent.

---

## Comment démarrer

### Dans GitHub Codespaces (recommandé, zéro setup)

Le devcontainer fait tout automatiquement à l'ouverture :
- Python 3.11 + boto3 + pytest installés
- Terraform 1.9.5 + AWS CLI dans le PATH
- LocalStack démarre via `docker compose up -d localstack`
- La fixture CSV est régénérée

Tu ouvres le Codespace, tu attends ~90 s, tu codes.

### En local

```bash
# 1. Pré-requis : Docker + docker compose + Terraform 1.6+ + Python 3.11
pip install -r requirements.txt

# 2. Lancer LocalStack
docker compose up -d localstack

# 3. Générer la fixture (déterministe — seed = 42)
python -m fixtures.generate_fixtures

# 4. Lancer la rubric d'évaluation (cassera tant que ton code est NotImplementedError)
pytest tests/ -v
```

Quand tes 6 tests passent en local, **commit + push** sur ton fork. La CI
GitHub Actions rejoue la même rubric — sur un runner vierge, avec une stack
LocalStack neuve — et l'app IAmDataEng affiche le verdict dans ton dashboard.

---

## L'architecture

```
                  PutObject CSV
                      |
                      v
   +---------------------------------------+
   |  s3://sobral-deliveries/incoming/*    |
   +---------------------------------------+
                      |
        S3 event notification (ObjectCreated:*)
                      |
                      v
              +---------------+
              |  Lambda       |
              |  sobral_ingest|
              +---------------+
               |             |
   parse_row valide      parse_row None (malformée)
               |             |
               v             v
       batch_write_item    PutObject
       DynamoDB            s3://.../dead-letter/<basename>.csv
       delivery_events
       (pk=truck_id, sk=delivery_id)
```

Lis cette boîte 30 secondes. Toute la suite n'est que la mise en code de ce
schéma.

---

## Les 6 checks de la rubric

Définis dans `tests/test_evaluate.py`. Chaque échec produit un message
pédagogique en clair.

| # | Id | Ce qu'on vérifie |
|---|---|---|
| 1 | `terraform_apply_succeeds_against_localstack` | `terraform init && apply` exit 0 contre les endpoints LocalStack, et les 3 outputs (bucket, table, fonction) sont déclarés. |
| 2 | `dynamodb_row_count_matches` | Après upload de la fixture, la table `delivery_events` contient **exactement 1198 items**. Ni plus (mauvaise validation) ni moins (UnprocessedItems ignorés). |
| 3 | `malformed_rows_quarantined` | Le préfixe `dead-letter/` contient **un seul** objet, avec **2 lignes** — les 2 lignes pourries de la fixture. Pas zéro (tu jettes la donnée), pas N (un fichier par ligne pourrie c'est polluant). |
| 4 | `lambda_completes_under_timeout` | La durée d'exécution Lambda est < 10 s pour 1200 lignes. Au-delà, c'est que tu fais `put_item` en boucle au lieu de `batch_write_item`. |
| 5 | `iam_role_least_privilege` | Static check sur `main.tf` : pas de `actions = ["*"]` ni `resources = ["*"]`. LocalStack n'enforce pas l'IAM, mais on évalue ta discipline. |
| 6 | `readme_documents_localstack_boundary` | Le `README.fr.md` contient une section listant ≥ 2 limitations LocalStack Community vs AWS réel (voir plus bas, à compléter). |

---

## Les pièges que les juniors se prennent

Vu dix fois en revue de code :

- **Croire que LocalStack === AWS.** LocalStack Community n'enforce PAS l'IAM
  par défaut. Une Lambda avec une policy vide écrira quand même dans
  DynamoDB. Sur le vrai AWS, ça aurait échoué en `AccessDenied`. C'est
  PRÉCISÉMENT pour ça que la rubric check 5 fait un static check de ta
  policy : si LocalStack ne te punit pas, le reviewer humain doit.

- **`put_item` dans une boucle de 1200 lignes.** 1200 round-trips réseau,
  même sur un loopback c'est ~30 s. Tu vas timeout. `batch_write_item`
  prend 25 items max par appel — c'est la limite DynamoDB, pas un détail
  optionnel.

- **Ignorer `UnprocessedItems`.** Sous throttling (et même sans, parfois,
  selon les versions DynamoDB), `batch_write_item` te renvoie une partie de
  tes items dans `UnprocessedItems`. Ce n'est PAS une erreur — c'est un
  signal "retry-moi ça". Boucle jusqu'à liste vide, avec un compteur
  d'essais max pour ne pas boucler à l'infini en cas de vraie panne.

- **Crash sur la 1ʳᵉ ligne pourrie.** Si ton handler `raise` au lieu de
  quarantine, S3 va te re-livrer l'event en boucle (Lambda retries) et tu
  n'ingéreras jamais les 1198 bonnes lignes. La ligne pourrie est de la
  donnée, pas une exception.

- **`s3.get_object(...)["Body"].read()` puis split en mémoire.** Sur 1200
  lignes ça marche. Sur 10 millions ça OOM. Apprends le streaming maintenant :
  `csv.DictReader(io.TextIOWrapper(obj["Body"]))` — pas de full-read.

- **Commit du `.tfstate` dans Git.** Il est dans `.gitignore` mais on l'a
  vu plus d'une fois force-commité. Le state contient des IDs de
  resources, parfois des secrets en clair. Jamais.

- **`terraform apply` qui n'est pas idempotent.** Un `local-exec` avec
  `aws s3 cp` à chaque apply, un `random_id` sans `keepers`, un timestamp
  dans un nom de resource — tous ces patterns cassent la promesse "apply
  deux fois = même résultat". La CI le vérifie indirectement (un
  re-apply ne doit pas casser le state).

- **Prétendre sur ton CV "j'ai construit sur AWS".** Sois précis : *"j'ai
  construit contre les APIs AWS en utilisant LocalStack Community pour le
  dev local et la CI"*. Les recruteurs sérieux respectent la précision.
  Les recruteurs faciles à berner ne sont pas ceux que tu veux.

---

## Différences vs le vrai AWS

Cette section est **requise par la rubric** (check 6). Tu dois lister au
moins **2 limitations** distinctes parmi ce qui suit, en les détaillant pour
montrer que tu sais ce que LocalStack ne t'a PAS appris :

- **IAM non-enforced en Community.** LocalStack Community ne vérifie pas les
  permissions par défaut. Sur le vrai AWS, une Lambda sans `s3:GetObject`
  sur le bon ARN reçoit `AccessDenied` en runtime, pas en `terraform apply`.
  Conséquence : ton check IAM doit être *statique* (lecture de la policy)
  ou *dynamique en intégration sur AWS sandbox*, pas dynamique en local.

- **Latence et coût réseau.** Sur LocalStack tout est sur loopback (~0.1 ms).
  Sur AWS, un appel `batch_write_item` cross-region prend 5-50 ms. Le coût
  cumulé de 1200 round-trips devient sensible. C'est pour ça que `batch_*`
  existe — pas seulement pour respecter une limite, mais pour amortir la
  latence.

- **KMS / chiffrement S3 (SSE).** Par défaut, un bucket LocalStack accepte
  des objets non-chiffrés. Sur AWS, une org sérieuse impose `aws:kms` via
  une bucket policy ; ton Terraform doit déclarer `aws_s3_bucket_server_side_encryption_configuration`
  et ta policy IAM doit autoriser `kms:GenerateDataKey` sur la bonne clé.

- **Concurrence Lambda et throttling.** LocalStack n'applique pas les
  limites de concurrence par défaut. Sur AWS, une rafale d'uploads S3 peut
  saturer ta concurrence Lambda et déclencher des erreurs `TooManyRequestsException`
  — tu dois soit `reserved_concurrent_executions`, soit gérer le throttling
  côté S3 (DLQ SQS).

- **Observability.** Les logs CloudWatch Lambda sur LocalStack ne sont pas
  bit-identiques à ceux d'AWS (format de la ligne `REPORT`, par exemple).
  Pour ton observability prod, prévois X-Ray + métriques custom — ce
  n'est pas testable en local.

- **VPC / network isolation.** LocalStack ne simule pas les VPC endpoints,
  les security groups, ni les NAT gateways. Sur AWS, si ta Lambda est
  dans un VPC privé pour parler à une RDS, tu dois déclarer un VPC
  endpoint S3 et DynamoDB (sinon le trafic part par internet).

Choisis tes 2-3 angles préférés et détaille-les. Un paragraphe par limitation,
pas une liste à puces sèche.

---

## Pour aller plus loin

Aucune lecture n'est obligatoire pour valider ce projet. Mais si tu veux
comprendre POURQUOI les patterns ci-dessus existent :

- **Joe Reis & Matt Housley**, *Fundamentals of Data Engineering* (O'Reilly,
  2022) — **chap. 7 « Ingestion », pp. 230-255** : ingestion event-driven,
  batch via S3-triggered Lambda, idempotence côté consumer.
- **Yan Cui**, *Production-Ready Serverless* — patterns d'error handling
  Lambda, DLQ, idempotence côté handler (les patterns sont les mêmes même
  si tu testes sur LocalStack).
- **AWS docs** :
  - [Lambda + S3 event source](https://docs.aws.amazon.com/lambda/latest/dg/with-s3.html)
  - [DynamoDB BatchWriteItem](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_BatchWriteItem.html) — note bien la section sur `UnprocessedItems`.
- **LocalStack docs** : [Community feature coverage](https://docs.localstack.cloud/user-guide/aws/feature-coverage/)
  — la matrice qui te dit explicitement ce qui est Community vs Pro.
- **Terraform docs** : [AWS provider — endpoints](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/guides/custom-service-endpoints) — comment rediriger vers LocalStack.

---

## Si tu es bloqué

Le projet est calibré pour ~10 h. Si tu tournes en rond plus d'1h30 sur un
check précis :

1. Relis le message d'erreur du test — il pointe presque toujours la cause.
2. Inspecte LocalStack à la main :
   - `aws --endpoint-url=http://localhost:4566 s3 ls s3://sobral-deliveries/`
   - `aws --endpoint-url=http://localhost:4566 dynamodb scan --table-name delivery_events`
   - `aws --endpoint-url=http://localhost:4566 logs tail /aws/lambda/sobral_ingest`
3. Ouvre une issue dans ton fork avec le label `help-wanted` — la communauté
   IAmDataEng y passe.

Bonne route.
