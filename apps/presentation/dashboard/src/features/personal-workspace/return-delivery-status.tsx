import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceReturnDelivery } from "./personal-workspace-model";

export function needsOriginalDeliveryProof(delivery?: Pick<WorkspaceReturnDelivery, "error" | "status">) {
  return delivery?.error === "initial_delivery_receipt_unavailable"
    && delivery.status !== "delivered" && delivery.status !== "superseded";
}

export function ReturnDeliveryStatus({ delivery }: { delivery?: WorkspaceReturnDelivery }) {
  const { t, locale } = useWorkspaceI18n();
  if (!delivery) return null;
  const needsProof = needsOriginalDeliveryProof(delivery);
  const label = needsProof ? t("returnDelivery.originalHandoff") : delivery.status === "delivered"
    ? delivery.verification === "reconciled_after_restart"
      ? t("returnDelivery.reconciled")
      : t("returnDelivery.delivered")
    : delivery.status === "verification_required"
      ? t("returnDelivery.verifying")
      : delivery.status === "explicit_unverified"
        ? t("returnDelivery.unverified")
        : t("returnDelivery.queued");
  const tone = needsProof ? "explicit_unverified" : delivery.status === "delivered"
    ? "delivered"
    : delivery.status === "verification_required"
      ? "verification_required"
      : delivery.status === "explicit_unverified"
        ? "explicit_unverified"
        : "queued";
  return (
    <small className={`personal-return-delivery is-${tone}`} role="status">
      {label}
      {delivery.request_revision !== undefined ? ` · ${locale === "zh-CN" ? "请求版本" : "Request revision"} ${delivery.request_revision ?? (locale === "zh-CN" ? "未知" : "unknown")}` : ""}
    </small>
  );
}
