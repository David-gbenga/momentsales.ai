import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { Centered } from "@/components/centered";
import { TokenAction } from "@/components/token-action";

export default async function ConfirmWaitlistPage() {
  const t = await getTranslations("waitlist");
  return (
    <Centered title="momentsales.ai waitlist">
      <Suspense>
        <TokenAction
          endpoint="/waitlist/confirm"
          pending={t("confirming")}
          success={t("confirmed")}
          failure={t("invalid")}
        />
      </Suspense>
    </Centered>
  );
}
