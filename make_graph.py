# pip install requests tqdm networkx pandas
import time
import math
import requests
import networkx as nx
from itertools import combinations
from tqdm import tqdm

# -----------------------------
# Config
# -----------------------------
USER_AGENT = "your-app-name/1.0 (you@example.com)"  # set a real UA/email per Nominatim policy
GEOCODE_SLEEP_SEC = 1.1  # be nice to the service

# Example input: replace with your own list or read from CSV
addresses = [
    "10 Downing Street, London, SW1A 2AA, UK",
    "Buckingham Palace, London, SW1A 1AA, UK",
    "Manchester Piccadilly Station, UK",
    "1 St Andrew's Road, Cambridge CB4 1DL, UK"
]

# -----------------------------
# Helpers
# -----------------------------
def geocode_address(addr):
    """Geocode an address via Nominatim. Returns (lat, lon) floats or None."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": addr, "format": "jsonv2", "addressdetails": 0, "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two WGS84 points, in kilometers."""
    R = 6371.0088  # mean Earth radius (km)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# -----------------------------
# Geocode (with simple cache & rate limiting)
# -----------------------------
cache = {}
coords = {}
for addr in tqdm(addresses, desc="Geocoding"):
    if addr in cache:
        coords[addr] = cache[addr]
        continue
    try:
        result = geocode_address(addr)
        cache[addr] = result
        coords[addr] = result
    except requests.HTTPError as e:
        print(f"HTTP error for '{addr}': {e}")
        coords[addr] = None
    except Exception as e:
        print(f"Error for '{addr}': {e}")
        coords[addr] = None
    time.sleep(GEOCODE_SLEEP_SEC)

# Filter out failures
resolved = {a: c for a, c in coords.items() if c is not None}
unresolved = [a for a, c in coords.items() if c is None]
if unresolved:
    print("Could not geocode:", unresolved)

# -----------------------------
# Build the graph
# -----------------------------
G = nx.Graph()
# Add nodes with attributes
for addr, (lat, lon) in resolved.items():
    G.add_node(addr, lat=lat, lon=lon)

# Option A: fully connected (complete) graph with distance weights
for a, b in combinations(resolved.keys(), 2):
    (lat1, lon1) = resolved[a]
    (lat2, lon2) = resolved[b]
    dist_km = haversine_km(lat1, lon1, lat2, lon2)
    G.add_edge(a, b, weight=dist_km)

# ---- Optional alternatives ----
# Option B (sparse): only connect if within a threshold, e.g., <= 50 km
# THRESH_KM = 50
# for a, b in combinations(resolved.keys(), 2):
#     (lat1, lon1) = resolved[a]
#     (lat2, lon2) = resolved[b]
#     dist_km = haversine_km(lat1, lon1, lat2, lon2)
#     if dist_km <= THRESH_KM:
#         G.add_edge(a, b, weight=dist_km)

# Option C (sparse): k-nearest neighbors per node (k=3)
# k = 3
# for a in resolved.keys():
#     distances = []
#     (lat1, lon1) = resolved[a]
#     for b in resolved.keys():
#         if a == b:
#             continue
#         (lat2, lon2) = resolved[b]
#         distances.append((b, haversine_km(lat1, lon1, lat2, lon2)))
#     distances.sort(key=lambda x: x[1])
#     for b, dist_km in distances[:k]:
#         G.add_edge(a, b, weight=dist_km)

# -----------------------------
# Done: you now have a weighted relational graph
# -----------------------------
print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
# Example: shortest path by physical distance between two addresses
if len(G) >= 2:
    src, dst = list(G.nodes())[:2]
    path = nx.shortest_path(G, src, dst, weight="weight")
    length = nx.path_weight(G, path, weight="weight")
    print("Example path:", " -> ".join(path), f"({length:.2f} km)")

# Save to GraphML (Gephi, Cytoscape, NetworkX can read)
nx.write_graphml(G, "uk_addresses_distance.graphml")
print("Wrote uk_addresses_distance.graphml")
