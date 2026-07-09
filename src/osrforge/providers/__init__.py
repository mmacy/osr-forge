"""Model providers: the seam between the pipeline and any LLM vendor.

Pipeline code never imports a vendor SDK — all model access goes through the
[`ModelProvider`][osrforge.providers.base.ModelProvider] protocol, and
[`providers.foundry`][osrforge.providers.foundry] is the only module allowed to
import `openai`/`azure.identity`. Import symbols from their home modules — this
package exports nothing.
"""
