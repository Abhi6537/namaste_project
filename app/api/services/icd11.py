import httpx
import logging
from typing import List, Optional
from fastapi import HTTPException

from app.api.models.common import ICD11Term
from app.core.config import settings

logger = logging.getLogger(__name__)


class ICD11Service:
    def __init__(self):
        self.base_url = settings.ICD11_BASE_URL
        self.client_id = settings.ICD11_CLIENT_ID
        self.client_secret = settings.ICD11_CLIENT_SECRET
        self.token_url = "https://icdaccessmanagement.who.int/connect/token"
        self._token_cache = None

    async def get_token(self) -> str:
        """Fetch OAuth2 token for ICD-11 API with caching."""
        if self._token_cache:
            return self._token_cache
            
        if not self.client_id or not self.client_secret:
            logger.error("❌ Missing ICD-11 credentials in settings")
            raise HTTPException(status_code=500, detail="ICD-11 credentials not configured")

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "icdapi_access",
                "grant_type": "client_credentials",
            }

            try:
                response = await client.post(self.token_url, headers=headers, data=data)
                
                if response.status_code != 200:
                    logger.error(f"❌ Failed to fetch ICD-11 token: {response.status_code} {response.text}")
                    raise HTTPException(status_code=500, detail="ICD-11 token request failed")

                token_data = response.json()
                token = token_data.get("access_token")
                if not token:
                    logger.error("❌ No access_token found in ICD-11 response")
                    raise HTTPException(status_code=500, detail="ICD-11 token missing in response")

                self._token_cache = token
                logger.info("✅ ICD-11 access token fetched successfully")
                return token
                
            except httpx.RequestError as e:
                logger.error(f"❌ Network error fetching ICD-11 token: {e}")
                raise HTTPException(status_code=500, detail="Network error connecting to ICD-11")

    async def search_icd11(self, query: str, use_flexisearch: bool = True) -> List[ICD11Term]:
        """Search ICD-11 database using WHO API."""
        if not query or query.strip() == "":
            return []
            
        try:
            token = await self.get_token()
            
            # Try multiple endpoint variations
            endpoints_to_try = [
                f"{self.base_url}/mms/search",
                f"{self.base_url}/search", 
                f"{self.base_url}/mms/flexisearch",
                f"{self.base_url}/release/11/2024-01/mms/search"
            ]

            params = {
                "q": query.strip(),
                "subtreeFilterUsesFoundationDescendants": "false",
                "includeKeywordResult": "true",
                "useFlexisearch": str(use_flexisearch).lower()
            }
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "API-Version": "v2",
                "Accept-Language": "en"
            }

            results: List[ICD11Term] = []
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                for url in endpoints_to_try:
                    try:
                        logger.info(f"🔎 Trying ICD-11 endpoint: {url}, query: '{query}'")
                        response = await client.get(url, headers=headers, params=params)
                        
                        if response.status_code == 200:
                            data = response.json()
                            logger.info(f"✅ Success with endpoint: {url}")
                            
                            # Handle different response formats
                            entities = (
                                data.get("destinationEntities", []) or 
                                data.get("entities", []) or 
                                data.get("searchResults", []) or
                                data.get("results", [])
                            )

                            for item in entities:
                                # Extract title - handle different formats
                                title = ""
                                if isinstance(item.get("title"), dict):
                                    title = item["title"].get("@value", "")
                                else:
                                    title = item.get("title", "") or item.get("name", "")
                                
                                # Extract definition
                                definition = ""
                                if isinstance(item.get("definition"), dict):
                                    definition = item["definition"].get("@value", "")
                                else:
                                    definition = item.get("definition", "")
                                
                                # Extract synonyms
                                synonyms = []
                                synonym_data = item.get("synonym", []) or item.get("synonyms", [])
                                if isinstance(synonym_data, list):
                                    for syn in synonym_data:
                                        if isinstance(syn, dict):
                                            synonyms.append(syn.get("label", {}).get("@value", ""))
                                        else:
                                            synonyms.append(str(syn))
                                
                                if title:  # Only add if we have a title
                                    icd_term = ICD11Term(
                                        id=item.get("id", "") or item.get("@id", ""),
                                        uri=item.get("uri", "") or item.get("id", "") or item.get("@id", ""),
                                        code=item.get("theCode", "") or item.get("code", ""),
                                        title=title,
                                        definition=definition,
                                        parent=item.get("parent", ""),
                                        children=item.get("children", []),
                                        synonyms=synonyms
                                    )
                                    results.append(icd_term)
                            
                            break  # Exit loop if we got results
                            
                        else:
                            logger.warning(f"⚠️ Endpoint {url} returned {response.status_code}: {response.text[:200]}")
                            
                    except httpx.RequestError as e:
                        logger.warning(f"⚠️ Network error with endpoint {url}: {e}")
                        continue
                    except Exception as e:
                        logger.warning(f"⚠️ Error with endpoint {url}: {e}")
                        continue

            if not results:
                logger.warning(f"⚠️ No ICD-11 results found for query='{query}' across all endpoints")
            else:
                logger.info(f"✅ Found {len(results)} ICD-11 matches for query='{query}'")
            
            return results

        except Exception as e:
            logger.error(f"❌ Unexpected error in ICD-11 search: {e}")
            return []  # Return empty list instead of raising exception