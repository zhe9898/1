/**
 * @description Frontend dev logger. Production is silent by default.
 */
type LogArgs = unknown[];

const isDev = import.meta.env.DEV;

export function logInfo(message: string, ...args: LogArgs): void {
  if (!isDev) return;
  console.info(message, ...args);
}

export function logWarn(message: string, ...args: LogArgs): void {
  if (!isDev) return;
  console.warn(message, ...args);
}

export function logError(message: string, ...args: LogArgs): void {
  if (!isDev) return;
  console.error(message, ...args);
}
