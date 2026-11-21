/** An error that maps to an HTTP status. Thrown anywhere below the HTTP layer. */
export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Sent along with the message, for an error the page handles itself instead of just showing. */
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
  }
}
