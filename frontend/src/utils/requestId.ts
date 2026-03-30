/** 每次调用生成新的 X-Request-ID，用于请求追踪与日志关联。 */
export function getRequestId(): string {
  return crypto.randomUUID();
}
