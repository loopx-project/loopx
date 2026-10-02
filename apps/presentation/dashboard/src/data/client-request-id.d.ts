export function clientRequestId(source?: Pick<Crypto, "getRandomValues"> & Partial<Pick<Crypto, "randomUUID">>): string;
