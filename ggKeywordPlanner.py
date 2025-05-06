import yaml
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException


def get_keyword_ideas(seed_keywords: list) -> None:
    """Generate keyword ideas (aka ‘keyword ideation’) for a list of seed terms. Documentation at https://developers.google.com/google-ads/api/reference/rpc/v19/KeywordPlan"""
    # ---- 1)  Read your Ads YAML credentials ---------------------------------
    with open("./google-ads.yaml", "r") as f:
        config_dict = yaml.safe_load(f)

    my_customer_id = config_dict.get("my_customer_id")

    # ---- 2)  Build a GoogleAdsClient ----------------------------------------
    client = GoogleAdsClient.load_from_dict(config_dict)
    keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

    # ---- 3)  Create a GenerateKeywordIdeasRequest ---------------------------
    request = client.get_type("GenerateKeywordIdeasRequest")
    request.customer_id = my_customer_id

    # • Seed keywords
    request.keyword_seed.keywords.extend(seed_keywords)

    # • Locale filters (same as before)
    request.language = "languageConstants/1040"              # 1000 =en-US, 1040 = vi-VN
    request.geo_target_constants.append("geoTargetConstants/2704")  # Việt Nam
    request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH

    # ---- 4)  Call the API ----------------------------------------------------
    try:
        response = keyword_plan_idea_service.generate_keyword_ideas(request=request)

        print(f"Keyword-ideation results for seeds: {', '.join(seed_keywords)}\n")
        for idea in response.results:
            text = idea.text
            print(f"Keyword idea: {text}")


    except GoogleAdsException as ex:
        print(f"Request ID '{ex.request_id}' failed with status '{ex.error.code().name}'")
        for error in ex.failure.errors:
            print(f"  → {error.message}")
        exit(1)


if __name__ == "__main__":
    seeds = ["dịch vụ fanpage"]
    get_keyword_ideas(seeds)
