import { CONNECTORS, JOBS, NODES, SETTINGS, SYSTEM } from "@/utils/api";

export interface ControlPlaneSurface {
  routePath: string;
  routeName: string;
  label: string;
  title: string;
  kicker: string;
  description: string;
  endpoint: string;
  adminOnly?: boolean;
}

export const CONTROL_PLANE_SURFACES: ControlPlaneSurface[] = [
  {
    routePath: "/",
    routeName: "dashboard",
    label: "Dashboard",
    title: "Dashboard",
    kicker: "Core",
    description: "网关控制面入口。",
    endpoint: SYSTEM.capabilities,
  },
  {
    routePath: "/nodes",
    routeName: "nodes",
    label: "Nodes",
    title: "Nodes",
    kicker: "Fleet",
    description: "查看 Runner 和接入代理的注册与心跳状态。",
    endpoint: NODES.list,
  },
  {
    routePath: "/jobs",
    routeName: "jobs",
    label: "Jobs",
    title: "Jobs",
    kicker: "Queue",
    description: "聚焦任务分发、执行回执和失败处理。",
    endpoint: JOBS.list,
  },
  {
    routePath: "/connectors",
    routeName: "connectors",
    label: "Connectors",
    title: "Connectors",
    kicker: "Bridge",
    description: "管理可选插件和客户端接入点。",
    endpoint: CONNECTORS.list,
  },
  {
    routePath: "/settings",
    routeName: "settings",
    label: "Settings",
    title: "Settings",
    kicker: "Core",
    description: "网关核心配置入口。",
    endpoint: SETTINGS.system,
    adminOnly: true,
  },
];
