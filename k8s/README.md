# TaskForge — Kubernetes Manifests

Plain Kubernetes manifests that mirror the `docker-compose.yml` topology: an
API Deployment, a Celery Worker Deployment, Postgres, and Redis, all wired
together with a ConfigMap/Secret and scaled by an HPA.

## Prerequisites

- A running cluster (e.g. [kind](https://kind.sigs.k8s.io/), minikube, or a
  managed cluster) with `kubectl` pointed at it.
- The `metrics-server` addon installed, if you want `hpa.yaml` to actually
  scale (most managed clusters ship it; kind/minikube need it added).
- The app image built and available to the cluster. It is **not** pulled
  from a registry by default (`imagePullPolicy: IfNotPresent`), so load it
  in first:

  ```bash
  docker build -t taskforge:latest .
  kind load docker-image taskforge:latest        # kind
  # or: minikube image load taskforge:latest      # minikube
  ```

## Apply

Resources are self-contained (each sets `namespace: taskforge`), so applying
the whole directory in one shot works regardless of file order:

```bash
kubectl apply -f k8s/
```

Watch rollout status:

```bash
kubectl -n taskforge get pods -w
```

## Access the API

```bash
kubectl -n taskforge port-forward svc/taskforge-api 8000:8000
curl http://localhost:8000/health
```

## Notes

- `secret.yaml` contains a **demo** password (`taskforge_secret`) in
  plaintext to keep this self-contained. Replace it with a sealed-secret or
  an External Secrets Operator source before using this anywhere real.
- Postgres uses a `StatefulSet` with a `PersistentVolumeClaim`; Redis and the
  app tiers are plain `Deployment`s since they don't need stable storage/identity.
- `taskforge-worker` has no `Service` — it has no HTTP surface, so it's
  reached only via the Redis broker.
- Teardown: `kubectl delete -f k8s/` (the Postgres PVC is not deleted
  automatically unless you also delete the `PersistentVolumeClaim` it created).
