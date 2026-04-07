export interface ClusterOverview {
  mode: "standalone" | "swarm" | "k3s";
  current_node: string;
  node_count: number;
  configured_nodes: number;
  healthy_nodes: number;
  capabilities: string[];
}
