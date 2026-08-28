import { describe, expect, it, vi } from "vitest";
import { api, setSessionExpiredHandler } from "../client";
import { ApiError } from "../types";
import { readTokens, writeTokens } from "../tokens";

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ERROR_BODY = {
  error: { code: "unauthorized", message: "Authentication required.", fields: null },
};

describe("api client", () => {
  it("throws ApiError carrying the code and fields from the envelope", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        { error: { code: "validation_error", message: "Invalid.", fields: { age: ["too low"] } } },
        422,
      ),
    );

    try {
      await api.get("/user/profile");
      expect.unreachable("expected the request to reject");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(422);
      expect((err as ApiError).fieldError("age")).toBe("too low");
    }
  });

  it("refreshes once on 401 and replays the request", async () => {
    writeTokens({ accessToken: "stale", refreshToken: "refresh-1" });

    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(ERROR_BODY, 401))
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "fresh", refresh_token: "refresh-2" }, 200),
      )
      .mockResolvedValueOnce(jsonResponse({ complete: true, missing_fields: [] }, 200));

    const result = await api.get<{ complete: boolean }>("/user/profile/completeness");

    expect(result.complete).toBe(true);
    expect(readTokens()?.accessToken).toBe("fresh");
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("gives up and clears the session when the refresh also fails", async () => {
    writeTokens({ accessToken: "stale", refreshToken: "refresh-1" });
    const onExpired = vi.fn();
    setSessionExpiredHandler(onExpired);

    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(ERROR_BODY, 401))
      .mockResolvedValueOnce(jsonResponse(ERROR_BODY, 401));

    await expect(api.get("/user/profile")).rejects.toBeInstanceOf(ApiError);

    expect(readTokens()).toBeNull();
    expect(onExpired).toHaveBeenCalledOnce();
    // Two calls only: the original and the failed refresh. No retry loop.
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
