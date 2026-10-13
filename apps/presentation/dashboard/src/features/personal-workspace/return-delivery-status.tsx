import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceReturnDelivery } from "./personal-workspace-model";

export function ReturnDeliveryStatus({ delivery }: { delivery?: WorkspaceReturnDelivery }) {
  const { t, locale } = useWorkspaceI18n();
  if (!delivery) return null;
  const label = delivery.status === "delivered"
    ? delivery.verification === "reconciled_after_restart"
      ? t("returnDelivery.reconciled")
      : t("returnDelivery.delivered")
    : delivery.status === "verification_required"
      ? t("returnDelivery.verifying")
      : delivery.status === "explicit_unverified"
        ? t("returnDelivery.unverified")
        : t("returnDelivery.queued");
  const tone = delivery.status === "delivered"
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
