import yaml
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException


def get_search_volume_for_keywords(keywords: list):
    # 1) Parse the YAML
    with open("./google-ads.yaml", "r") as f:
        config_dict = yaml.safe_load(f)

    # 2) Extract your custom ID
    my_customer_id = config_dict.get("my_customer_id")

    # 3) Create the GoogleAdsClient from the dictionary
    client = GoogleAdsClient.load_from_dict(config_dict)

    # 4) Now use `my_customer_id`
    keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

    request = client.get_type("GenerateKeywordHistoricalMetricsRequest")
    request.customer_id = my_customer_id
    request.keywords.extend(keywords)

    historical_metrics_options = client.get_type("HistoricalMetricsOptions")
    historical_metrics_options.include_average_cpc = False

    # Set the 'start' and 'end' YearMonth values
    year_month_range = historical_metrics_options.year_month_range
    year_month_range.start.year = 2024
    year_month_range.start.month = "JANUARY"
    year_month_range.end.year = 2025
    year_month_range.end.month = "JANUARY"

    request.historical_metrics_options = historical_metrics_options

    request.language = "languageConstants/1040"
    # 1000 for English, 1040 for Vietnamese
    request.geo_target_constants.append("geoTargetConstants/2704")
    request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH

    try:
        response = keyword_plan_idea_service.generate_keyword_historical_metrics(request=request)
        for result in response.results:
            keyword_text = result.text
            metrics = result.keyword_metrics
            print(f"Keyword: {keyword_text}")
            if metrics:
                print(f"  Avg Monthly Searches: {metrics.avg_monthly_searches}")
                print(f"  Competition: {metrics.competition.name}")
                print(f"  Low CPC (micros): {metrics.low_top_of_page_bid_micros}")
                print(f"  High CPC (micros): {metrics.high_top_of_page_bid_micros}")
            print("-" * 40)

    except GoogleAdsException as ex:
        print(f"Request with ID '{ex.request_id}' failed with status '{ex.error.code().name}'")
        for error in ex.failure.errors:
            print(f"  Error: {error.message}")
        exit(1)


if __name__ == "__main__":
    keywords_to_check = ["Nước mắm", "Review nước mắm", "Nước mắm nguyên chất"]
    get_search_volume_for_keywords(keywords_to_check)
    
