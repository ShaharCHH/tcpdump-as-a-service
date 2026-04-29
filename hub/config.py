from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "openshift" uses OAuth2 redirect flow; "kubernetes" uses token paste + TokenReview
    auth_mode: str = "kubernetes"

    # --- OpenShift OAuth (only needed when auth_mode="openshift") ---
    # Base URL of the OpenShift API server, e.g. https://api.mycluster.example.com:6443
    openshift_api_url: str = ""
    oauth_client_id: str = "tcpdump-hub"
    oauth_client_secret: str = ""
    # Full URL the browser is redirected back to after OpenShift login
    oauth_callback_url: str = ""

    # --- Kubernetes client ---
    # True when running inside the cluster (uses mounted service account token).
    # Set False for local development with a local kubeconfig.
    k8s_in_cluster: bool = True
    k8s_namespace: str = "tcpdump-service"

    # --- Capture limits ---
    max_duration_minutes: int = 30
    max_concurrent_captures: int = 10
    max_concurrent_per_user: int = 3

    # --- pcap file storage ---
    pcap_storage_path: str = "/pcaps"
    pcap_ttl_hours: int = 24
    pcap_max_files: int = 10       # -W flag passed to tcpdump
    pcap_max_file_mb: int = 50     # -C flag passed to tcpdump (MB)
    pcap_rotation_seconds: int = 60  # -G flag passed to tcpdump (seconds)

    # --- Session security ---
    # Change this to a long random string in production.
    secret_key: str = "changeme-in-production"
    session_max_age_hours: int = 8

    # --- Agent communication ---
    # In-cluster WebSocket URL agents use to reach the hub service, e.g. ws://tcpdump-hub:8080
    hub_ws_url: str = ""
    # Port the agent's local HTTP server listens on (for pcap file download by the hub)
    agent_http_port: int = 8081

    # --- Admin access ---
    # Comma-separated k8s usernames that may view the audit log in the UI.
    admin_users: str = ""

    @property
    def admin_user_list(self) -> list[str]:
        return [u.strip() for u in self.admin_users.split(",") if u.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
