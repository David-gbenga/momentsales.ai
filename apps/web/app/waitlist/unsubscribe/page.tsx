import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { Centered } from "@/components/centered";
import { TokenAction } from "@/components/token-action";

export default async function UnsubscribePage() {
  const t = await getTranslations("waitlist");
  return (
    <Centered title="momentsales.ai waitlist">
      <Suspense>
        <TokenAction
          endpoint="/waitlist/unsubscribe"
          pending={t("confirming")}
          success={t("unsubscribed")}
          failure={t("invalid")}
        />
      </Suspense>
    </Centered>
  );
}
